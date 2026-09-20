/* SPDX-License-Identifier: MIT
 *
 * C ABI wrapper around the Blackmagic RAW COM API.
 *
 * Decode follows the official OpenEXRTranscode sample default:
 * Linear gamma + ACES AP0 gamut, post-3D LUT disabled, RGBF32.
 * Pipeline: Metal (macOS) or CUDA then OpenCL (Win/Linux), CPU fallback.
 *
 * Do not invent COM calls — job flow matches ExtractFrame / OpenEXRTranscode:
 *   OpenClip → CreateJobReadFrame → Submit → ReadComplete
 *     → SetResourceFormat / SetResolutionScale
 *     → CreateJobDecodeAndProcessFrame → Submit → ProcessComplete
 *   Release job pointers before FlushJobs (otherwise FlushJobs deadlocks).
 */

#include "braw_bridge.h"

#include "BlackmagicRawAPI.h"

#include <atomic>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

#if defined(_WIN32)
#  include <ole2.h>
#  include <oleauto.h>
#  include "BlackmagicRawAPIDispatch.h"
/* Win SDK uses COM VARIANT; Mac/Linux headers typedef their own Variant. */
using Variant = VARIANT;
#elif defined(__APPLE__)
#  include <CoreFoundation/CoreFoundation.h>
#endif

namespace {

thread_local std::string g_last_error;
std::mutex g_init_mu;
IBlackmagicRawFactory *g_factory = nullptr;
IBlackmagicRawPipelineDevice *g_device = nullptr;
const char *g_decoder_kind = "cpu";
std::string g_sdk_version;
bool g_initialized = false;
#if defined(_WIN32)
bool g_com_inited = false;
#endif

void set_error(const std::string &msg) { g_last_error = msg; }

void clear_error() { g_last_error.clear(); }

void copy_cstr(char *dst, size_t dst_len, const std::string &src) {
    if (dst == nullptr || dst_len == 0) {
        return;
    }
    const size_t n = src.size() < dst_len - 1 ? src.size() : dst_len - 1;
    std::memcpy(dst, src.data(), n);
    dst[n] = '\0';
}

BlackmagicRawResolutionScale mode_to_scale(int mode) {
    switch (mode) {
        case BRAW_DECODE_HALF:
            return blackmagicRawResolutionScaleHalf;
        case BRAW_DECODE_QUARTER:
            return blackmagicRawResolutionScaleQuarter;
        case BRAW_DECODE_EIGHTH:
            return blackmagicRawResolutionScaleEighth;
        case BRAW_DECODE_FULL:
        default:
            return blackmagicRawResolutionScaleFull;
    }
}

unsigned scale_divisor(int mode) {
    switch (mode) {
        case BRAW_DECODE_HALF:
            return 2;
        case BRAW_DECODE_QUARTER:
            return 4;
        case BRAW_DECODE_EIGHTH:
            return 8;
        default:
            return 1;
    }
}

#if defined(_WIN32)
using BmdStr = BSTR;

BmdStr make_bmd_string(const char *utf8) {
    if (utf8 == nullptr) {
        return nullptr;
    }
    /* -1 includes the trailing NUL; SysAllocStringLen adds its own terminator. */
    const int needed = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, nullptr, 0);
    if (needed <= 0) {
        return nullptr;
    }
    const UINT wchar_count = static_cast<UINT>(needed - 1);
    BSTR s = SysAllocStringLen(nullptr, wchar_count);
    if (s != nullptr) {
        MultiByteToWideChar(CP_UTF8, 0, utf8, -1, s, needed);
    }
    return s;
}

void free_bmd_string(BmdStr s) {
    if (s) {
        SysFreeString(s);
    }
}

std::string bmd_string_utf8(BmdStr s) {
    if (s == nullptr) {
        return {};
    }
    const UINT len = SysStringLen(s);
    const int n = WideCharToMultiByte(CP_UTF8, 0, s, static_cast<int>(len), nullptr, 0, nullptr, nullptr);
    std::string out(static_cast<size_t>(n), '\0');
    if (n > 0) {
        WideCharToMultiByte(CP_UTF8, 0, s, static_cast<int>(len), out.data(), n, nullptr, nullptr);
    }
    return out;
}

#elif defined(__APPLE__)
using BmdStr = CFStringRef;

BmdStr make_bmd_string(const char *utf8) {
    if (utf8 == nullptr) {
        return nullptr;
    }
    return CFStringCreateWithCString(kCFAllocatorDefault, utf8, kCFStringEncodingUTF8);
}

void free_bmd_string(BmdStr s) {
    if (s) {
        CFRelease(s);
    }
}

std::string bmd_string_utf8(BmdStr s) {
    if (s == nullptr) {
        return {};
    }
    const char *fast = CFStringGetCStringPtr(s, kCFStringEncodingUTF8);
    if (fast) {
        return fast;
    }
    const CFIndex len = CFStringGetLength(s);
    const CFIndex max = CFStringGetMaximumSizeForEncoding(len, kCFStringEncodingUTF8) + 1;
    std::string out(static_cast<size_t>(max), '\0');
    if (CFStringGetCString(s, out.data(), max, kCFStringEncodingUTF8)) {
        out.resize(std::strlen(out.c_str()));
        return out;
    }
    return {};
}

#else
using BmdStr = const char *;

BmdStr make_bmd_string(const char *utf8) { return utf8; }

void free_bmd_string(BmdStr) {}

std::string bmd_string_utf8(BmdStr s) { return s ? std::string(s) : std::string(); }
#endif

class OwnedBmdString {
public:
    explicit OwnedBmdString(const char *utf8) : m_str(make_bmd_string(utf8)) {}
    ~OwnedBmdString() {
#if defined(_WIN32) || defined(__APPLE__)
        free_bmd_string(m_str);
#endif
    }
    OwnedBmdString(const OwnedBmdString &) = delete;
    OwnedBmdString &operator=(const OwnedBmdString &) = delete;
    BmdStr get() const { return m_str; }

private:
    BmdStr m_str;
};

/* REFIID is GUID/IID& on Win/Linux and CFUUIDBytes on Mac — no operator== on Mac. */
bool iid_equals(REFIID a, REFIID b) { return std::memcmp(&a, &b, sizeof(a)) == 0; }

bool iid_is_iunknown(REFIID iid) {
#if defined(_WIN32)
    return iid_equals(iid, IID_IUnknown);
#elif defined(__APPLE__)
#  if defined(IUnknownUUID)
    const CFUUIDBytes unknown = CFUUIDGetUUIDBytes(IUnknownUUID);
    return std::memcmp(&iid, &unknown, sizeof(unknown)) == 0;
#  elif defined(IID_IUnknown)
    return iid_equals(iid, IID_IUnknown);
#  else
    /* COM IUnknown: 00000000-0000-0000-C000-000000000046 */
    static const CFUUIDBytes k_iunknown = {
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46};
    return std::memcmp(&iid, &k_iunknown, sizeof(k_iunknown)) == 0;
#  endif
#else
    return iid_equals(iid, IID_IUnknown);
#endif
}

std::string variant_to_string(const Variant &value) {
    switch (value.vt) {
        case blackmagicRawVariantTypeString:
            return bmd_string_utf8(value.bstrVal);
        case blackmagicRawVariantTypeU8:
#if defined(_WIN32)
            return std::to_string(static_cast<unsigned>(value.bVal));
#else
            return std::to_string(static_cast<unsigned>(value.uiVal));
#endif
        case blackmagicRawVariantTypeS16:
            return std::to_string(value.iVal);
        case blackmagicRawVariantTypeU16:
            return std::to_string(value.uiVal);
        case blackmagicRawVariantTypeS32:
            return std::to_string(value.intVal);
        case blackmagicRawVariantTypeU32:
            return std::to_string(value.uintVal);
        case blackmagicRawVariantTypeFloat32:
            return std::to_string(value.fltVal);
        case blackmagicRawVariantTypeFloat64:
            return std::to_string(value.dblVal);
        default:
            return {};
    }
}

HRESULT set_string_attr(
    IBlackmagicRawClipProcessingAttributes *attrs,
    BlackmagicRawClipProcessingAttribute key,
    const char *text) {
    OwnedBmdString owned(text);
    Variant value;
    VariantInit(&value);
    value.vt = blackmagicRawVariantTypeString;
    value.bstrVal = owned.get();
    return attrs->SetClipAttribute(key, &value);
}

HRESULT apply_linear_aces_ap0(IBlackmagicRawClip *clip) {
    /* Official OpenEXRTranscode default (no -m): Linear + ACES AP0, LUT off. */
    IBlackmagicRawClipProcessingAttributes *attrs = nullptr;
    HRESULT hr = clip->QueryInterface(
        IID_IBlackmagicRawClipProcessingAttributes, reinterpret_cast<void **>(&attrs));
    if (FAILED(hr) || attrs == nullptr) {
        set_error("Failed to get IBlackmagicRawClipProcessingAttributes");
        return FAILED(hr) ? hr : E_FAIL;
    }

    hr = set_string_attr(attrs, blackmagicRawClipProcessingAttributeGamma, "Linear");
    if (SUCCEEDED(hr)) {
        hr = set_string_attr(attrs, blackmagicRawClipProcessingAttributeGamut, "ACES AP0");
    }
    if (SUCCEEDED(hr)) {
        hr = set_string_attr(attrs, blackmagicRawClipProcessingAttributePost3DLUTMode, "Disabled");
    }
    attrs->Release();
    if (FAILED(hr)) {
        set_error("Failed to set Linear / ACES AP0 / Disabled 3DLUT clip attributes");
    }
    return hr;
}

struct DecodeRequest {
    float *out = nullptr;
    size_t out_bytes = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    int rc = -1;
    std::string err;
    BlackmagicRawResolutionScale scale = blackmagicRawResolutionScaleFull;
};

class Clip;

class Callback : public IBlackmagicRawCallback {
public:
    explicit Callback(Clip *owner) : m_owner(owner) {}

    void ReadComplete(IBlackmagicRawJob *readJob, HRESULT result, IBlackmagicRawFrame *frame) override;
    void ProcessComplete(
        IBlackmagicRawJob *job, HRESULT result, IBlackmagicRawProcessedImage *processedImage) override;

    void ReadAudioComplete(IBlackmagicRawJob *, HRESULT, IBlackmagicRawAudioBuffer *) override {}
    void DecodeComplete(IBlackmagicRawJob *, HRESULT) override {}
    void TrimProgress(IBlackmagicRawJob *, float) override {}
    void TrimComplete(IBlackmagicRawJob *, HRESULT) override {}
    void SidecarMetadataParseWarning(
        IBlackmagicRawClip *,
#if defined(__APPLE__)
        CFStringRef,
#elif defined(_WIN32)
        BSTR,
#else
        const char *,
#endif
        uint32_t,
#if defined(__APPLE__)
        CFStringRef
#elif defined(_WIN32)
        BSTR
#else
        const char *
#endif
    ) override {}
    void SidecarMetadataParseError(
        IBlackmagicRawClip *,
#if defined(__APPLE__)
        CFStringRef,
#elif defined(_WIN32)
        BSTR,
#else
        const char *,
#endif
        uint32_t,
#if defined(__APPLE__)
        CFStringRef
#elif defined(_WIN32)
        BSTR
#else
        const char *
#endif
    ) override {}
    void PreparePipelineComplete(void *, HRESULT) override {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, LPVOID *ppvOut) override {
        if (ppvOut == nullptr) {
            return E_POINTER;
        }
        if (iid_is_iunknown(iid)) {
            *ppvOut = static_cast<IUnknown *>(this);
            AddRef();
            return S_OK;
        }
        if (iid_equals(iid, IID_IBlackmagicRawCallback)) {
            *ppvOut = static_cast<IBlackmagicRawCallback *>(this);
            AddRef();
            return S_OK;
        }
        *ppvOut = nullptr;
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override { return ++m_ref; }

    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG n = --m_ref;
        if (n == 0) {
            delete this;
        }
        return n;
    }

private:
    Clip *m_owner = nullptr;
    std::atomic<ULONG> m_ref{1};
};

struct Clip {
    IBlackmagicRaw *codec = nullptr;
    IBlackmagicRawClip *clip = nullptr;
    Callback *callback = nullptr;
    uint32_t width = 0;
    uint32_t height = 0;
    uint64_t frame_count = 0;
    float fps = 24.0f;
    std::string camera_type;
    std::mutex decode_mu;
    DecodeRequest *pending = nullptr;
};

void Callback::ReadComplete(IBlackmagicRawJob *readJob, HRESULT result, IBlackmagicRawFrame *frame) {
    DecodeRequest *req = nullptr;
    if (readJob != nullptr) {
        readJob->GetUserData(reinterpret_cast<void **>(&req));
    }
    if (req == nullptr) {
        return;
    }

    if (result == E_UNEXPECTED) {
        req->err = "Dropped / missing BRAW frame";
        req->rc = -2;
        return;
    }
    if (FAILED(result) || frame == nullptr) {
        req->err = "BRAW read job failed";
        req->rc = static_cast<int>(result);
        return;
    }

    HRESULT hr = frame->SetResourceFormat(blackmagicRawResourceFormatRGBF32);
    if (SUCCEEDED(hr)) {
        hr = frame->SetResolutionScale(req->scale);
    }

    IBlackmagicRawJob *decodeJob = nullptr;
    if (SUCCEEDED(hr)) {
        hr = frame->CreateJobDecodeAndProcessFrame(nullptr, nullptr, &decodeJob);
    }
    if (SUCCEEDED(hr) && decodeJob != nullptr) {
        decodeJob->SetUserData(req);
        hr = decodeJob->Submit();
        decodeJob->Release();
        decodeJob = nullptr;
    }
    if (FAILED(hr)) {
        req->err = "BRAW decode/process submit failed";
        req->rc = static_cast<int>(hr);
    }
}

void Callback::ProcessComplete(
    IBlackmagicRawJob * /*job*/, HRESULT result, IBlackmagicRawProcessedImage *processedImage) {
    DecodeRequest *req = m_owner != nullptr ? m_owner->pending : nullptr;
    if (req == nullptr) {
        return;
    }
    if (FAILED(result) || processedImage == nullptr) {
        req->err = "BRAW process job failed";
        req->rc = FAILED(result) ? static_cast<int>(result) : -1;
        return;
    }

    uint32_t width = 0;
    uint32_t height = 0;
    void *image = nullptr;
    HRESULT hr = processedImage->GetWidth(&width);
    if (SUCCEEDED(hr)) {
        hr = processedImage->GetHeight(&height);
    }
    if (SUCCEEDED(hr)) {
        hr = processedImage->GetResource(&image);
    }
    if (FAILED(hr) || image == nullptr || width == 0 || height == 0) {
        req->err = "BRAW processed image has no RGBF32 resource";
        req->rc = FAILED(hr) ? static_cast<int>(hr) : -1;
        return;
    }

    const size_t need = static_cast<size_t>(width) * static_cast<size_t>(height) * 3u * sizeof(float);
    if (req->out == nullptr || req->out_bytes < need) {
        req->err = "BRAW decode buffer too small";
        req->rc = -3;
        return;
    }
    std::memcpy(req->out, image, need);
    req->width = width;
    req->height = height;
    req->rc = 0;
}

bool force_cpu() {
    const char *e = std::getenv("EXR_CONVERTER_BRAW_CPU");
    if (e == nullptr || e[0] == '\0') {
        return false;
    }
    return !(e[0] == '0' && e[1] == '\0');
}

const char *kind_name(BlackmagicRawPipeline pipeline) {
    switch (pipeline) {
        case blackmagicRawPipelineMetal:
            return "metal";
        case blackmagicRawPipelineCUDA:
            return "cuda";
        case blackmagicRawPipelineOpenCL:
            return "opencl";
        default:
            return "cpu";
    }
}

void release_pipeline_device() {
    if (g_device != nullptr) {
        g_device->Release();
        g_device = nullptr;
    }
    g_decoder_kind = "cpu";
}

IBlackmagicRawPipelineDevice *try_create_device(
    IBlackmagicRawFactory *factory, BlackmagicRawPipeline pipeline)
{
    IBlackmagicRawPipelineDeviceIterator *it = nullptr;
    if (FAILED(factory->CreatePipelineDeviceIterator(
            pipeline, blackmagicRawInteropNone, &it))
        || it == nullptr) {
        return nullptr;
    }
    IBlackmagicRawPipelineDevice *dev = nullptr;
    const HRESULT hr = it->CreateDevice(&dev);
    it->Release();
    if (FAILED(hr) || dev == nullptr) {
        return nullptr;
    }
    (void)dev->SetBestInstructionSet();
    return dev;
}

void select_pipeline_device() {
    release_pipeline_device();
    if (force_cpu() || g_factory == nullptr) {
        return;
    }
#if defined(__APPLE__)
    const BlackmagicRawPipeline order[] = {
        blackmagicRawPipelineMetal,
        blackmagicRawPipelineOpenCL,
    };
#else
    const BlackmagicRawPipeline order[] = {
        blackmagicRawPipelineCUDA,
        blackmagicRawPipelineOpenCL,
    };
#endif
    for (BlackmagicRawPipeline pipeline : order) {
        IBlackmagicRawPipelineDevice *dev = try_create_device(g_factory, pipeline);
        if (dev != nullptr) {
            g_device = dev;
            g_decoder_kind = kind_name(pipeline);
            return;
        }
    }
}

HRESULT apply_cpu_pipeline(IBlackmagicRawConfiguration *cfg) {
#if defined(_WIN32)
    BOOL cpu_ok = FALSE;
#else
    bool cpu_ok = false;
#endif
    if (SUCCEEDED(cfg->IsPipelineSupported(blackmagicRawPipelineCPU, &cpu_ok)) && cpu_ok) {
        const HRESULT hr = cfg->SetPipeline(blackmagicRawPipelineCPU, nullptr, nullptr);
        if (FAILED(hr)) {
            set_error("SetPipeline(CPU) failed");
            return hr;
        }
    }
    return S_OK;
}

HRESULT configure_pipeline(IBlackmagicRaw *codec) {
    IBlackmagicRawConfiguration *cfg = nullptr;
    HRESULT hr = codec->QueryInterface(IID_IBlackmagicRawConfiguration, reinterpret_cast<void **>(&cfg));
    if (FAILED(hr) || cfg == nullptr) {
        set_error("Failed to get IBlackmagicRawConfiguration");
        return FAILED(hr) ? hr : E_FAIL;
    }

    if (g_device != nullptr) {
        hr = cfg->SetFromDevice(g_device);
        if (FAILED(hr)) {
            /* Soft fallback: keep converting on CPU if GPU setup fails. */
            release_pipeline_device();
            hr = apply_cpu_pipeline(cfg);
            if (FAILED(hr)) {
                cfg->Release();
                return hr;
            }
        }
    } else {
        hr = apply_cpu_pipeline(cfg);
        if (FAILED(hr)) {
            cfg->Release();
            return hr;
        }
    }

    if (g_sdk_version.empty()) {
#if defined(_WIN32)
        BSTR ver = nullptr;
        if (SUCCEEDED(cfg->GetVersion(&ver)) && ver != nullptr) {
            g_sdk_version = bmd_string_utf8(ver);
            SysFreeString(ver);
        }
#elif defined(__APPLE__)
        CFStringRef ver = nullptr;
        if (SUCCEEDED(cfg->GetVersion(&ver)) && ver != nullptr) {
            g_sdk_version = bmd_string_utf8(ver);
            CFRelease(ver);
        }
#else
        const char *ver = nullptr;
        if (SUCCEEDED(cfg->GetVersion(&ver)) && ver != nullptr) {
            g_sdk_version = ver;
        }
#endif
    }
    cfg->Release();
    return S_OK;
}

Clip *as_clip(void *handle) { return static_cast<Clip *>(handle); }

} // namespace

int braw_bridge_available(void) { return 1; }

int braw_bridge_initialize(const char *libs_path) {
    std::lock_guard<std::mutex> lock(g_init_mu);
    if (g_initialized && g_factory != nullptr) {
        return 0;
    }
    clear_error();
    if (libs_path == nullptr || libs_path[0] == '\0') {
        set_error("BRAW libs path is empty");
        return -1;
    }

#if defined(_WIN32)
    if (!g_com_inited) {
        const HRESULT com_hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        if (com_hr != S_OK && com_hr != S_FALSE) {
            set_error("CoInitializeEx failed");
            return -1;
        }
        g_com_inited = true;
    }
#endif

#if defined(_WIN32) || defined(__APPLE__)
    OwnedBmdString path(libs_path);
    g_factory = CreateBlackmagicRawFactoryInstanceFromPath(path.get());
#else
    g_factory = CreateBlackmagicRawFactoryInstanceFromPath(libs_path);
#endif
    if (g_factory == nullptr) {
        set_error(
            std::string("CreateBlackmagicRawFactoryInstanceFromPath failed (") + libs_path
            + "). Check libBlackmagicRawAPI is in that folder.");
        return -1;
    }

    select_pipeline_device();

    IBlackmagicRaw *probe = nullptr;
    if (SUCCEEDED(g_factory->CreateCodec(&probe)) && probe != nullptr) {
        configure_pipeline(probe);
        probe->Release();
    }

    g_initialized = true;
    return 0;
}

void braw_bridge_finalize(void) {
    std::lock_guard<std::mutex> lock(g_init_mu);
    if (g_factory != nullptr) {
        g_factory->Release();
        g_factory = nullptr;
    }
    release_pipeline_device();
    g_initialized = false;
#if defined(_WIN32)
    if (g_com_inited) {
        CoUninitialize();
        g_com_inited = false;
    }
#endif
}

int braw_bridge_is_initialized(void) { return g_initialized && g_factory != nullptr ? 1 : 0; }

void braw_bridge_sdk_version(char *buf, size_t buf_len) { copy_cstr(buf, buf_len, g_sdk_version); }

void *braw_bridge_open(const char *utf8_path) {
    clear_error();
    if (!braw_bridge_is_initialized()) {
        set_error("BRAW bridge is not initialized");
        return nullptr;
    }
    if (utf8_path == nullptr || utf8_path[0] == '\0') {
        set_error("Empty BRAW path");
        return nullptr;
    }

    auto *out = new Clip();
    HRESULT hr = g_factory->CreateCodec(&out->codec);
    if (FAILED(hr) || out->codec == nullptr) {
        set_error("CreateCodec failed");
        delete out;
        return nullptr;
    }
    if (FAILED(configure_pipeline(out->codec))) {
        out->codec->Release();
        delete out;
        return nullptr;
    }

    out->callback = new Callback(out);
    hr = out->codec->SetCallback(out->callback);
    if (FAILED(hr)) {
        set_error("SetCallback failed");
        out->callback->Release();
        out->codec->Release();
        delete out;
        return nullptr;
    }

    OwnedBmdString path(utf8_path);
    hr = out->codec->OpenClip(path.get(), &out->clip);
    if (FAILED(hr) || out->clip == nullptr) {
        set_error(std::string("OpenClip failed: ") + utf8_path);
        out->codec->SetCallback(nullptr);
        out->callback->Release();
        out->codec->Release();
        delete out;
        return nullptr;
    }

    if (FAILED(apply_linear_aces_ap0(out->clip))) {
        braw_bridge_close(out);
        return nullptr;
    }

    if (FAILED(out->clip->GetWidth(&out->width)) || FAILED(out->clip->GetHeight(&out->height))
        || FAILED(out->clip->GetFrameCount(&out->frame_count))
        || FAILED(out->clip->GetFrameRate(&out->fps))) {
        set_error("Failed to read BRAW clip geometry / frame count / fps");
        braw_bridge_close(out);
        return nullptr;
    }
    if (out->fps <= 0.0f) {
        out->fps = 24.0f;
    }

    BmdStr cam = nullptr;
    if (SUCCEEDED(out->clip->GetCameraType(&cam)) && cam != nullptr) {
        out->camera_type = bmd_string_utf8(cam);
#if defined(_WIN32) || defined(__APPLE__)
        free_bmd_string(cam);
#endif
    }
    return out;
}

void braw_bridge_close(void *handle) {
    Clip *clip = as_clip(handle);
    if (clip == nullptr) {
        return;
    }
    if (clip->codec != nullptr) {
        clip->codec->SetCallback(nullptr);
        clip->codec->FlushJobs();
    }
    if (clip->clip != nullptr) {
        clip->clip->Release();
        clip->clip = nullptr;
    }
    if (clip->callback != nullptr) {
        clip->callback->Release();
        clip->callback = nullptr;
    }
    if (clip->codec != nullptr) {
        clip->codec->Release();
        clip->codec = nullptr;
    }
    delete clip;
}

int braw_bridge_clip_info(void *handle, BRAWBridgeClipInfo *out) {
    clear_error();
    Clip *clip = as_clip(handle);
    if (clip == nullptr || out == nullptr || clip->clip == nullptr) {
        set_error("Invalid BRAW clip handle");
        return -1;
    }
    std::memset(out, 0, sizeof(*out));
    out->width = clip->width;
    out->height = clip->height;
    out->frame_count = clip->frame_count > 0xFFFFFFFFu ? 0xFFFFFFFFu : static_cast<uint32_t>(clip->frame_count);
    out->fps = clip->fps;
    copy_cstr(out->sdk_version, sizeof(out->sdk_version), g_sdk_version);
    copy_cstr(out->colorspace_hint, sizeof(out->colorspace_hint), "ACES2065-1");
    copy_cstr(out->camera_type, sizeof(out->camera_type), clip->camera_type);
    return 0;
}

size_t braw_bridge_decode_buffer_bytes(
    void *handle, int decode_mode, uint32_t *out_w, uint32_t *out_h) {
    Clip *clip = as_clip(handle);
    if (clip == nullptr || clip->clip == nullptr) {
        return 0;
    }
    uint32_t w = clip->width;
    uint32_t h = clip->height;

    IBlackmagicRawClipResolutions *res = nullptr;
    if (SUCCEEDED(clip->clip->QueryInterface(
            IID_IBlackmagicRawClipResolutions, reinterpret_cast<void **>(&res)))
        && res != nullptr) {
        uint32_t rw = 0;
        uint32_t rh = 0;
        if (SUCCEEDED(res->GetClosestResolutionForScale(mode_to_scale(decode_mode), &rw, &rh)) && rw && rh) {
            w = rw;
            h = rh;
        }
        res->Release();
    } else {
        const unsigned d = scale_divisor(decode_mode);
        w = w / d;
        h = h / d;
        if (w < 1) {
            w = 1;
        }
        if (h < 1) {
            h = 1;
        }
    }
    if (out_w) {
        *out_w = w;
    }
    if (out_h) {
        *out_h = h;
    }
    return static_cast<size_t>(w) * static_cast<size_t>(h) * 3u * sizeof(float);
}

int braw_bridge_decode_frame(
    void *handle,
    uint32_t frame_index,
    int decode_mode,
    float *out_rgb_f32,
    size_t out_rgb_bytes,
    uint32_t *out_w,
    uint32_t *out_h) {
    clear_error();
    Clip *clip = as_clip(handle);
    if (clip == nullptr || clip->clip == nullptr || clip->codec == nullptr) {
        set_error("Invalid BRAW clip handle");
        return -1;
    }
    if (out_rgb_f32 == nullptr) {
        set_error("Null decode buffer");
        return -1;
    }
    if (static_cast<uint64_t>(frame_index) >= clip->frame_count) {
        set_error("BRAW frame index out of range");
        return -1;
    }

    std::lock_guard<std::mutex> lock(clip->decode_mu);
    DecodeRequest req;
    req.out = out_rgb_f32;
    req.out_bytes = out_rgb_bytes;
    req.scale = mode_to_scale(decode_mode);
    clip->pending = &req;

    IBlackmagicRawJob *readJob = nullptr;
    HRESULT hr = clip->clip->CreateJobReadFrame(frame_index, &readJob);
    if (FAILED(hr) || readJob == nullptr) {
        clip->pending = nullptr;
        set_error("CreateJobReadFrame failed");
        return -1;
    }

    IBlackmagicRawReadJobHints *hints = nullptr;
    if (SUCCEEDED(readJob->QueryInterface(IID_IBlackmagicRawReadJobHints, reinterpret_cast<void **>(&hints)))
        && hints != nullptr) {
        hints->SetReaderResolutionScale(req.scale);
        hints->Release();
    }

    readJob->SetUserData(&req);
    hr = readJob->Submit();
    /* Release before FlushJobs or the wait never returns (see ExtractFrame). */
    readJob->Release();
    readJob = nullptr;
    if (FAILED(hr)) {
        clip->pending = nullptr;
        set_error("Submit read job failed");
        return -1;
    }

    clip->codec->FlushJobs();
    clip->pending = nullptr;

    if (req.rc != 0) {
        set_error(req.err.empty() ? "BRAW decode failed" : req.err);
        return req.rc;
    }
    if (out_w) {
        *out_w = req.width;
    }
    if (out_h) {
        *out_h = req.height;
    }
    return 0;
}

const char *braw_bridge_last_error(void) { return g_last_error.c_str(); }

const char *braw_bridge_decoder_kind(void) { return g_decoder_kind != nullptr ? g_decoder_kind : "cpu"; }

int braw_bridge_metadata_string(void *handle, const char *key, char *buf, size_t buf_len) {
    Clip *clip = as_clip(handle);
    if (clip == nullptr || clip->clip == nullptr || key == nullptr || buf == nullptr || buf_len == 0) {
        return -1;
    }
    buf[0] = '\0';

    if (std::strcmp(key, "camera_type") == 0 && !clip->camera_type.empty()) {
        copy_cstr(buf, buf_len, clip->camera_type);
        return 1;
    }

    OwnedBmdString key_str(key);
    Variant value;
    VariantInit(&value);
    const HRESULT hr = clip->clip->GetMetadata(key_str.get(), &value);
    if (FAILED(hr)) {
        VariantClear(&value);
        return 0;
    }
    const std::string text = variant_to_string(value);
    VariantClear(&value);
    if (text.empty()) {
        return 0;
    }
    copy_cstr(buf, buf_len, text);
    return 1;
}

int braw_bridge_timecode(void *handle, uint32_t frame_index, char *buf, size_t buf_len) {
    Clip *clip = as_clip(handle);
    if (clip == nullptr || clip->clip == nullptr || buf == nullptr || buf_len == 0) {
        return -1;
    }
    buf[0] = '\0';
    BmdStr tc = nullptr;
    if (FAILED(clip->clip->GetTimecodeForFrame(frame_index, &tc)) || tc == nullptr) {
        return 0;
    }
    copy_cstr(buf, buf_len, bmd_string_utf8(tc));
#if defined(_WIN32) || defined(__APPLE__)
    free_bmd_string(tc);
#endif
    return buf[0] != '\0' ? 1 : 0;
}
