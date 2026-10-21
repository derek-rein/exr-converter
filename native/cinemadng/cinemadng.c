/* CinemaDNG frame reader (uncompressed and lossless JPEG).
 *
 * This product includes DNG technology under license by Adobe.
 *
 * The Adobe DNG SDK is not linked here. This file reads CinemaDNG Bayer
 * frames (TIFF/DNG strips or tiles, compression none or lossless JPEG)
 * and returns black-normalized mosaic samples. Colour lives in Python.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#define CDNG_API __declspec(dllexport)
#define CDNG_TLS __declspec(thread)
#else
#define CDNG_API __attribute__((visibility("default")))
#define CDNG_TLS __thread
#endif

#define CDNG_CFA 32803u
#define CDNG_MAX_IFD 16
#define CDNG_MAX_BYTES (512u * 1024u * 1024u)

typedef struct cinemadng_meta {
    uint32_t width;
    uint32_t height;
    uint32_t bits;
    uint32_t compression;
    uint8_t cfa[4];
    int32_t has_forward;
    int32_t has_color_matrix;
    int32_t illuminant;
    int32_t orientation;
    int32_t has_neutral;
    double black[4];
    double white;
    double neutral[3];
    double color_matrix[9];
    double forward_matrix[9];
    double baseline_exposure;
    double frame_rate;
    char make[96];
    char model[96];
    char unique_model[128];
} cinemadng_meta;

typedef struct {
    const uint8_t *data;
    size_t size;
    int le;
} Tiff;

typedef struct {
    uint32_t width, height, bits, compression, photometric, samples;
    uint32_t rows_per_strip, orientation, tile_w, tile_h;
    uint32_t *offsets;
    uint32_t *counts;
    uint32_t nstrips;
    int tiled;
    uint8_t cfa[4];
    int has_cfa;
    double black[4];
    int black_rows, black_cols;
    double white;
    int has_white;
    double neutral[3];
    int has_neutral;
    double cm[9];
    int has_cm;
    double fm[9];
    int has_fm;
    int illuminant;
    double baseline;
    double fps;
    char make[96];
    char model[96];
    char unique[128];
    uint32_t active[4];
    int has_active;
    double crop_origin[2];
    double crop_size[2];
    int has_crop;
} RawIfd;

static CDNG_TLS char g_err[512];

static void set_error(const char *msg) {
    snprintf(g_err, sizeof g_err, "%s", msg ? msg : "CinemaDNG error");
}

CDNG_API const char *cinemadng_last_error(void) { return g_err; }

CDNG_API size_t cinemadng_meta_size(void) { return sizeof(cinemadng_meta); }

CDNG_API void cinemadng_free(void *p) { free(p); }

static uint16_t ru16(const Tiff *t, size_t off) {
    const uint8_t *p = t->data + off;
    if (t->le) {
        return (uint16_t)(p[0] | (p[1] << 8));
    }
    return (uint16_t)((p[0] << 8) | p[1]);
}

static uint32_t ru32(const Tiff *t, size_t off) {
    const uint8_t *p = t->data + off;
    if (t->le) {
        return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24));
    }
    return (uint32_t)((p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]);
}

static int in_range(const Tiff *t, size_t off, size_t n) {
    return off <= t->size && n <= t->size - off;
}

static size_t type_size(uint16_t type) {
    switch (type) {
    case 1:
    case 2:
    case 6:
    case 7:
        return 1;
    case 3:
    case 8:
        return 2;
    case 4:
    case 9:
    case 11:
    case 13:
        return 4;
    case 5:
    case 10:
    case 12:
        return 8;
    default:
        return 0;
    }
}

static int read_doubles(
    const Tiff *t, uint16_t type, uint32_t count, uint32_t value_or_off, double *out, int maxn
) {
    size_t es = type_size(type);
    size_t nbytes;
    size_t pos;
    int n;
    int i;
    if (es == 0 || count == 0 || maxn <= 0) {
        return 0;
    }
    n = (int)count;
    if (n > maxn) {
        n = maxn;
    }
    nbytes = (size_t)count * es;
    if (nbytes <= 4) {
        pos = 0;
        /* Inline value is stored in the 4-byte field, original endian. */
    } else {
        pos = value_or_off;
        if (!in_range(t, pos, (size_t)n * es)) {
            return -1;
        }
    }
    /* Inline values live in the 4-byte field. Rebuild that field in file order. */
    if (nbytes <= 4) {
        uint8_t inline_buf[4];
        if (t->le) {
            inline_buf[0] = (uint8_t)(value_or_off & 0xff);
            inline_buf[1] = (uint8_t)((value_or_off >> 8) & 0xff);
            inline_buf[2] = (uint8_t)((value_or_off >> 16) & 0xff);
            inline_buf[3] = (uint8_t)((value_or_off >> 24) & 0xff);
        } else {
            inline_buf[0] = (uint8_t)((value_or_off >> 24) & 0xff);
            inline_buf[1] = (uint8_t)((value_or_off >> 16) & 0xff);
            inline_buf[2] = (uint8_t)((value_or_off >> 8) & 0xff);
            inline_buf[3] = (uint8_t)(value_or_off & 0xff);
        }
        for (i = 0; i < n; i++) {
            size_t o = (size_t)i * es;
            if (type == 1 || type == 7) {
                out[i] = inline_buf[o];
            } else if (type == 3) {
                uint16_t v = t->le ? (uint16_t)(inline_buf[o] | (inline_buf[o + 1] << 8))
                                   : (uint16_t)((inline_buf[o] << 8) | inline_buf[o + 1]);
                out[i] = v;
            } else if (type == 4) {
                out[i] = value_or_off;
            } else if (type == 6) {
                out[i] = (int8_t)inline_buf[o];
            } else if (type == 8) {
                uint16_t u = t->le ? (uint16_t)(inline_buf[o] | (inline_buf[o + 1] << 8))
                                   : (uint16_t)((inline_buf[o] << 8) | inline_buf[o + 1]);
                out[i] = (int16_t)u;
            } else {
                return -1;
            }
        }
        return n;
    }
    for (i = 0; i < n; i++) {
        size_t o = pos + (size_t)i * es;
        if (type == 1 || type == 7) {
            out[i] = t->data[o];
        } else if (type == 3) {
            out[i] = ru16(t, o);
        } else if (type == 4) {
            out[i] = ru32(t, o);
        } else if (type == 5 || type == 10) {
            uint32_t num = ru32(t, o);
            uint32_t den = ru32(t, o + 4);
            double snum = type == 10 ? (double)(int32_t)num : (double)num;
            double sden = type == 10 ? (double)(int32_t)den : (double)den;
            out[i] = (sden == 0.0) ? 0.0 : snum / sden;
        } else if (type == 8) {
            out[i] = (int16_t)ru16(t, o);
        } else if (type == 9) {
            out[i] = (int32_t)ru32(t, o);
        } else if (type == 11) {
            uint32_t u = ru32(t, o);
            float f;
            memcpy(&f, &u, 4);
            out[i] = f;
        } else if (type == 12) {
            return -1;
        } else if (type == 6) {
            out[i] = (int8_t)t->data[o];
        } else {
            return -1;
        }
    }
    return n;
}

static int read_ascii(
    const Tiff *t, uint32_t count, uint32_t value_or_off, char *dst, size_t dst_n
) {
    size_t n;
    size_t pos;
    if (dst_n == 0) {
        return 0;
    }
    dst[0] = 0;
    if (count == 0) {
        return 0;
    }
    n = count;
    if (n >= dst_n) {
        n = dst_n - 1;
    }
    if (count <= 4) {
        uint8_t inline_buf[4];
        size_t i;
        if (t->le) {
            inline_buf[0] = (uint8_t)(value_or_off & 0xff);
            inline_buf[1] = (uint8_t)((value_or_off >> 8) & 0xff);
            inline_buf[2] = (uint8_t)((value_or_off >> 16) & 0xff);
            inline_buf[3] = (uint8_t)((value_or_off >> 24) & 0xff);
        } else {
            inline_buf[0] = (uint8_t)((value_or_off >> 24) & 0xff);
            inline_buf[1] = (uint8_t)((value_or_off >> 16) & 0xff);
            inline_buf[2] = (uint8_t)((value_or_off >> 8) & 0xff);
            inline_buf[3] = (uint8_t)(value_or_off & 0xff);
        }
        for (i = 0; i < n; i++) {
            dst[i] = (char)inline_buf[i];
        }
        dst[n] = 0;
        return 0;
    }
    pos = value_or_off;
    if (!in_range(t, pos, n)) {
        return -1;
    }
    memcpy(dst, t->data + pos, n);
    dst[n] = 0;
    return 0;
}

static int read_u32_array(
    const Tiff *t, uint16_t type, uint32_t count, uint32_t value_or_off, uint32_t **out
) {
    double tmp[1];
    uint32_t *arr;
    uint32_t i;
    size_t es;
    size_t pos;
    if (count == 0) {
        *out = NULL;
        return 0;
    }
    es = type_size(type);
    if (es == 0) {
        return -1;
    }
    arr = (uint32_t *)calloc(count, sizeof(uint32_t));
    if (!arr) {
        return -1;
    }
    if ((size_t)count * es <= 4) {
        int n = read_doubles(t, type, count, value_or_off, tmp, 1);
        if (n < 0) {
            free(arr);
            return -1;
        }
        /* count may be > 1 but still inline (shorts). Re-read properly. */
        {
            double small[4];
            int sn = read_doubles(t, type, count, value_or_off, small, 4);
            uint32_t k;
            if (sn < 0) {
                free(arr);
                return -1;
            }
            for (k = 0; k < count && k < 4; k++) {
                arr[k] = (uint32_t)small[k];
            }
        }
        *out = arr;
        return 0;
    }
    pos = value_or_off;
    if (!in_range(t, pos, (size_t)count * es)) {
        free(arr);
        return -1;
    }
    for (i = 0; i < count; i++) {
        size_t o = pos + (size_t)i * es;
        if (type == 3) {
            arr[i] = ru16(t, o);
        } else if (type == 4 || type == 13) {
            arr[i] = ru32(t, o);
        } else {
            free(arr);
            return -1;
        }
    }
    *out = arr;
    (void)tmp;
    return 0;
}

static void raw_clear(RawIfd *r) {
    memset(r, 0, sizeof(*r));
    r->neutral[0] = r->neutral[1] = r->neutral[2] = 1.0;
    r->cfa[0] = 0;
    r->cfa[1] = 1;
    r->cfa[2] = 1;
    r->cfa[3] = 2;
    r->orientation = 1;
    r->samples = 1;
    r->black_rows = 1;
    r->black_cols = 1;
}

static void raw_free(RawIfd *r) {
    free(r->offsets);
    free(r->counts);
    r->offsets = NULL;
    r->counts = NULL;
}

static int parse_ifd(const Tiff *t, uint32_t off, RawIfd *out, uint32_t *sub_offs, int *nsub, int sub_cap) {
    uint16_t nent;
    uint16_t i;
    if (!in_range(t, off, 2)) {
        set_error("CinemaDNG IFD is truncated");
        return -1;
    }
    raw_clear(out);
    nent = ru16(t, off);
    if (!in_range(t, off + 2, (size_t)nent * 12)) {
        set_error("CinemaDNG IFD entries are truncated");
        return -1;
    }
    for (i = 0; i < nent; i++) {
        size_t e = off + 2 + (size_t)i * 12;
        uint16_t tag = ru16(t, e);
        uint16_t type = ru16(t, e + 2);
        uint32_t count = ru32(t, e + 4);
        uint32_t val = ru32(t, e + 8);
        double d[16];
        int nd;
        if (tag == 256 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->width = (uint32_t)d[0];
        } else if (tag == 257 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->height = (uint32_t)d[0];
        } else if (tag == 258 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->bits = (uint32_t)d[0];
        } else if (tag == 259 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->compression = (uint32_t)d[0];
        } else if (tag == 262 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->photometric = (uint32_t)d[0];
        } else if (tag == 274 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->orientation = (uint32_t)d[0];
        } else if (tag == 277 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->samples = (uint32_t)d[0];
        } else if (tag == 278 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->rows_per_strip = (uint32_t)d[0];
        } else if (tag == 322 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->tile_w = (uint32_t)d[0];
        } else if (tag == 323 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->tile_h = (uint32_t)d[0];
        } else if (tag == 273 || tag == 324) {
            if (read_u32_array(t, type, count, val, &out->offsets) != 0) {
                set_error("CinemaDNG strip offsets are unreadable");
                return -1;
            }
            out->nstrips = count;
            out->tiled = tag == 324;
        } else if (tag == 279 || tag == 325) {
            if (read_u32_array(t, type, count, val, &out->counts) != 0) {
                set_error("CinemaDNG strip byte counts are unreadable");
                return -1;
            }
        } else if (tag == 330) {
            uint32_t *subs = NULL;
            uint32_t s;
            if (read_u32_array(t, type, count, val, &subs) != 0) {
                set_error("CinemaDNG SubIFDs are unreadable");
                return -1;
            }
            for (s = 0; subs && s < count && *nsub < sub_cap; s++) {
                sub_offs[(*nsub)++] = subs[s];
            }
            free(subs);
        } else if (tag == 271) {
            read_ascii(t, count, val, out->make, sizeof out->make);
        } else if (tag == 272) {
            read_ascii(t, count, val, out->model, sizeof out->model);
        } else if (tag == 50708) {
            read_ascii(t, count, val, out->unique, sizeof out->unique);
        } else if (tag == 33421 && (nd = read_doubles(t, type, count, val, d, 2)) >= 2) {
            (void)nd;
        } else if (tag == 33422) {
            uint8_t bytes[4];
            uint32_t k;
            size_t es = type_size(type);
            size_t nbytes = (size_t)count * (es ? es : 1);
            const uint8_t *src;
            uint8_t inline_buf[4];
            if (count >= 4) {
                if (nbytes <= 4) {
                    if (t->le) {
                        inline_buf[0] = (uint8_t)(val & 0xff);
                        inline_buf[1] = (uint8_t)((val >> 8) & 0xff);
                        inline_buf[2] = (uint8_t)((val >> 16) & 0xff);
                        inline_buf[3] = (uint8_t)((val >> 24) & 0xff);
                    } else {
                        inline_buf[0] = (uint8_t)((val >> 24) & 0xff);
                        inline_buf[1] = (uint8_t)((val >> 16) & 0xff);
                        inline_buf[2] = (uint8_t)((val >> 8) & 0xff);
                        inline_buf[3] = (uint8_t)(val & 0xff);
                    }
                    src = inline_buf;
                } else if (in_range(t, val, 4)) {
                    src = t->data + val;
                } else {
                    src = NULL;
                }
                if (src) {
                    for (k = 0; k < 4; k++) {
                        bytes[k] = src[k * (es ? es : 1)];
                        out->cfa[k] = bytes[k];
                    }
                    out->has_cfa = 1;
                }
            }
        } else if (tag == 50713 && (nd = read_doubles(t, type, count, val, d, 2)) >= 2) {
            out->black_rows = (int)d[0];
            out->black_cols = (int)d[1];
            if (out->black_rows < 1) {
                out->black_rows = 1;
            }
            if (out->black_cols < 1) {
                out->black_cols = 1;
            }
            if (out->black_rows > 2) {
                out->black_rows = 2;
            }
            if (out->black_cols > 2) {
                out->black_cols = 2;
            }
        } else if (tag == 50714 && (nd = read_doubles(t, type, count, val, d, 4)) > 0) {
            int k;
            for (k = 0; k < nd && k < 4; k++) {
                out->black[k] = d[k];
            }
        } else if (tag == 50717 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->white = d[0];
            out->has_white = 1;
        } else if (tag == 50728 && (nd = read_doubles(t, type, count, val, d, 3)) >= 3) {
            out->neutral[0] = d[0];
            out->neutral[1] = d[1];
            out->neutral[2] = d[2];
            out->has_neutral = 1;
        } else if (tag == 50721 && (nd = read_doubles(t, type, count, val, d, 9)) >= 9) {
            memcpy(out->cm, d, 9 * sizeof(double));
            out->has_cm = 1;
        } else if (tag == 50964 && (nd = read_doubles(t, type, count, val, d, 9)) >= 9) {
            memcpy(out->fm, d, 9 * sizeof(double));
            out->has_fm = 1;
        } else if (tag == 50778 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->illuminant = (int)d[0];
        } else if (tag == 50730 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->baseline = d[0];
        } else if (tag == 51044 && (nd = read_doubles(t, type, count, val, d, 1)) > 0) {
            out->fps = d[0];
        } else if (tag == 50829 && (nd = read_doubles(t, type, count, val, d, 4)) >= 4) {
            out->active[0] = (uint32_t)d[0];
            out->active[1] = (uint32_t)d[1];
            out->active[2] = (uint32_t)d[2];
            out->active[3] = (uint32_t)d[3];
            out->has_active = 1;
        } else if (tag == 50719 && (nd = read_doubles(t, type, count, val, d, 2)) >= 2) {
            out->crop_origin[0] = d[0];
            out->crop_origin[1] = d[1];
            out->has_crop = out->has_crop == 2 ? 2 : 1;
            if (out->crop_size[0] > 0 && out->crop_size[1] > 0) {
                out->has_crop = 2;
            }
        } else if (tag == 50720 && (nd = read_doubles(t, type, count, val, d, 2)) >= 2) {
            out->crop_size[0] = d[0];
            out->crop_size[1] = d[1];
            if (out->has_crop) {
                out->has_crop = 2;
            }
        }
        (void)nd;
    }
    return 0;
}


/* --- lossless JPEG (ITU-T T.81 SOF3, one component) --- */

typedef struct {
    const uint8_t *p;
    const uint8_t *end;
    uint32_t bitbuf;
    int bitcount;
} Bits;

typedef struct {
    int maxcode[17];
    int mincode[17];
    int valptr[17];
    uint8_t huffval[256];
    int ok;
} Huff;

static int pull_byte(Bits *b) {
    uint8_t c;
    if (b->p >= b->end) {
        return -1;
    }
    c = *b->p++;
    if (c == 0xFF) {
        if (b->p >= b->end) {
            return -1;
        }
        if (*b->p != 0x00) {
            b->p--;
            return -1;
        }
        b->p++;
    }
    b->bitbuf = (b->bitbuf << 8) | c;
    b->bitcount += 8;
    return 0;
}

static int get_bits(Bits *b, int n) {
    int v;
    if (n <= 0) {
        return 0;
    }
    while (b->bitcount < n) {
        if (pull_byte(b) != 0) {
            return -1;
        }
    }
    b->bitcount -= n;
    v = (int)((b->bitbuf >> b->bitcount) & ((1u << n) - 1u));
    return v;
}

static void build_huff(Huff *h, const uint8_t counts[16], const uint8_t *syms) {
    int code = 0;
    int k = 0;
    int len;
    memset(h, 0, sizeof(*h));
    for (len = 1; len <= 16; len++) {
        int n = counts[len - 1];
        if (n) {
            h->valptr[len] = k;
            h->mincode[len] = code;
            k += n;
            code += n;
            h->maxcode[len] = code - 1;
        } else {
            h->maxcode[len] = -1;
        }
        code <<= 1;
    }
    if (k > 256) {
        k = 256;
    }
    if (k > 0) {
        memcpy(h->huffval, syms, (size_t)k);
    }
    h->ok = 1;
}

static int decode_huff(Bits *b, const Huff *h) {
    int bit = get_bits(b, 1);
    int code;
    int len;
    if (bit < 0) {
        return -1;
    }
    code = bit;
    for (len = 1; len <= 16; len++) {
        if (h->maxcode[len] >= 0 && code <= h->maxcode[len]) {
            int idx = h->valptr[len] + code - h->mincode[len];
            if (idx < 0 || idx >= 256) {
                return -1;
            }
            return h->huffval[idx];
        }
        bit = get_bits(b, 1);
        if (bit < 0) {
            return -1;
        }
        code = (code << 1) | bit;
    }
    return -1;
}

static int ashift(int v) { return v >= 0 ? (v >> 1) : -((-v) >> 1); }

static int predict_sel(int which, int left, int above, int al) {
    switch (which) {
    case 1:
        return left;
    case 2:
        return above;
    case 3:
        return al;
    case 4:
        return left + above - al;
    case 5:
        return left + ashift(above - al);
    case 6:
        return above + ashift(left - al);
    case 7:
        return ashift(left + above);
    default:
        return left;
    }
}

static int skip_marker_payload(const uint8_t **p, const uint8_t *end) {
    uint16_t len;
    if (*p + 2 > end) {
        return -1;
    }
    len = (uint16_t)(((*p)[0] << 8) | (*p)[1]);
    if (len < 2 || *p + len > end) {
        return -1;
    }
    *p += len;
    return 0;
}

static float norm_sample(const RawIfd *ifd, uint32_t x, uint32_t y, float sample) {
    int cols = ifd->black_cols > 0 ? ifd->black_cols : 1;
    int rows = ifd->black_rows > 0 ? ifd->black_rows : 1;
    int bx = cols > 1 ? (int)(x % (uint32_t)cols) : 0;
    int by = rows > 1 ? (int)(y % (uint32_t)rows) : 0;
    int bi = by * cols + bx;
    double black = ifd->black[bi >= 0 && bi < 4 ? bi : 0];
    double white = ifd->has_white ? ifd->white : (double)((1u << ifd->bits) - 1u);
    double denom = white - black;
    if (denom < 1e-9) {
        denom = 1.0;
    }
    return (float)(((double)sample - black) / denom);
}

static int decode_ljpeg(
    const uint8_t *data, size_t len, float *dst, uint32_t stride, const RawIfd *ifd, uint32_t origin_x,
    uint32_t origin_y, uint32_t clip_w, uint32_t clip_h
) {
    const uint8_t *p;
    const uint8_t *end;
    Huff table;
    int precision = 0;
    int pred = 1;
    int pt = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    int restart = 0;
    int saw_sos = 0;
    uint32_t x;
    uint32_t y;
    int init;
    int mod;
    int *above = NULL;
    int *cur = NULL;
    int left = 0;
    int since = 0;
    Bits bits;
    int rc = -1;

    memset(&table, 0, sizeof table);
    if (len < 4 || data[0] != 0xFF || data[1] != 0xD8) {
        set_error("CinemaDNG lossless JPEG is missing SOI");
        return -1;
    }
    p = data + 2;
    end = data + len;
    while (p + 1 < end && !saw_sos) {
        uint8_t marker;
        if (*p != 0xFF) {
            set_error("CinemaDNG lossless JPEG marker parse failed");
            return -1;
        }
        while (p < end && *p == 0xFF) {
            p++;
        }
        if (p >= end) {
            return -1;
        }
        marker = *p++;
        if (marker == 0xD9 || (marker >= 0xD0 && marker <= 0xD7)) {
            continue;
        }
        if (marker == 0xDA) {
            uint16_t sos_len;
            int ncomp;
            if (p + 2 > end) {
                return -1;
            }
            sos_len = (uint16_t)((p[0] << 8) | p[1]);
            if (sos_len < 8 || p + sos_len > end) {
                return -1;
            }
            ncomp = p[2];
            if (ncomp != 1) {
                set_error("CinemaDNG lossless JPEG must be one component");
                return -1;
            }
            pred = p[5];
            pt = p[7] & 0x0F;
            p += sos_len;
            saw_sos = 1;
            break;
        }
        if (marker == 0xC3) {
            uint16_t flen;
            if (p + 8 > end) {
                return -1;
            }
            flen = (uint16_t)((p[0] << 8) | p[1]);
            if (flen < 8 || p + flen > end) {
                return -1;
            }
            precision = p[2];
            height = (uint32_t)((p[3] << 8) | p[4]);
            width = (uint32_t)((p[5] << 8) | p[6]);
            if (p[7] != 1) {
                set_error("CinemaDNG lossless JPEG must be one component");
                return -1;
            }
            p += flen;
            continue;
        }
        if (marker == 0xC4) {
            const uint8_t *hp;
            const uint8_t *hend;
            uint16_t flen;
            if (p + 2 > end) {
                return -1;
            }
            flen = (uint16_t)((p[0] << 8) | p[1]);
            if (flen < 19 || p + flen > end) {
                return -1;
            }
            hp = p + 2;
            hend = p + flen;
            while (hp < hend) {
                uint8_t counts[16];
                int total = 0;
                int ci;
                if ((hp[0] & 0xF0) != 0) {
                    set_error("CinemaDNG lossless JPEG Huffman table is not DC");
                    return -1;
                }
                if (hp + 17 > hend) {
                    return -1;
                }
                hp++;
                memcpy(counts, hp, 16);
                hp += 16;
                for (ci = 0; ci < 16; ci++) {
                    total += counts[ci];
                }
                if (total > 256 || hp + total > hend) {
                    return -1;
                }
                build_huff(&table, counts, hp);
                hp += total;
            }
            p += flen;
            continue;
        }
        if (marker == 0xDD) {
            if (p + 4 > end) {
                return -1;
            }
            restart = (p[2] << 8) | p[3];
            if (skip_marker_payload(&p, end) != 0) {
                return -1;
            }
            continue;
        }
        if (skip_marker_payload(&p, end) != 0) {
            set_error("CinemaDNG lossless JPEG marker is truncated");
            return -1;
        }
    }
    if (!saw_sos || !table.ok || width == 0 || height == 0 || precision < 2 || precision > 16 || pt < 0 ||
        pt >= precision) {
        set_error("CinemaDNG lossless JPEG frame header is incomplete");
        return -1;
    }
    above = (int *)calloc(width, sizeof(int));
    cur = (int *)calloc(width, sizeof(int));
    if (!above || !cur) {
        set_error("Out of memory decoding CinemaDNG");
        goto done;
    }
    memset(&bits, 0, sizeof bits);
    bits.p = p;
    bits.end = end;
    init = 1 << (precision - pt - 1);
    mod = 1 << (precision - pt);
    for (y = 0; y < height; y++) {
        for (x = 0; x < width; x++) {
            int cat;
            int diff = 0;
            int base;
            int val;
            int extra;
            if (restart > 0 && since == restart) {
                uint8_t mk;
                bits.bitbuf = 0;
                bits.bitcount = 0;
                if (bits.p >= bits.end || *bits.p != 0xFF) {
                    set_error("CinemaDNG lossless JPEG restart marker is missing");
                    goto done;
                }
                while (bits.p < bits.end && *bits.p == 0xFF) {
                    bits.p++;
                }
                if (bits.p >= bits.end) {
                    set_error("CinemaDNG lossless JPEG restart marker is missing");
                    goto done;
                }
                mk = *bits.p++;
                if (mk < 0xD0 || mk > 0xD7) {
                    set_error("CinemaDNG lossless JPEG restart marker is missing");
                    goto done;
                }
                since = 0;
            }
            if (since == 0) {
                base = init;
            } else if (x == 0) {
                base = above[x];
            } else if (y == 0) {
                base = left;
            } else {
                base = predict_sel(pred, left, above[x], above[x - 1]);
            }
            cat = decode_huff(&bits, &table);
            if (cat < 0 || cat > 16) {
                set_error("CinemaDNG lossless JPEG Huffman decode failed");
                goto done;
            }
            if (cat > 0) {
                extra = get_bits(&bits, cat);
                if (extra < 0) {
                    set_error("CinemaDNG lossless JPEG ran out of bits");
                    goto done;
                }
                if (extra < (1 << (cat - 1))) {
                    diff = extra - (1 << cat) + 1;
                } else {
                    diff = extra;
                }
            }
            val = base + diff;
            val %= mod;
            if (val < 0) {
                val += mod;
            }
            left = val;
            cur[x] = val;
            /* Edge tiles are padded to the tile size. Keep the predictor
               running across the pad, but do not write past the image. */
            if (x < clip_w && y < clip_h) {
                dst[(size_t)y * stride + x] =
                    norm_sample(ifd, origin_x + x, origin_y + y, (float)(val << pt));
            }
            since++;
        }
        memcpy(above, cur, width * sizeof(int));
    }
    rc = 0;
done:
    free(above);
    free(cur);
    return rc;
}

static int decode_uncompressed(
    const Tiff *t, const RawIfd *ifd, const uint8_t *bytes, size_t nbytes, float *dst, uint32_t origin_x,
    uint32_t origin_y, uint32_t width, uint32_t height
) {
    uint32_t bits = ifd->bits;
    uint32_t y;
    uint32_t x;
    size_t row_bytes;
    const uint8_t *row;
    if (bits == 0 || bits > 16) {
        set_error("CinemaDNG bit depth is not supported");
        return -1;
    }
    row_bytes = ((size_t)width * bits + 7u) / 8u;
    if (nbytes < row_bytes * height) {
        set_error("CinemaDNG strip is shorter than the image");
        return -1;
    }
    for (y = 0; y < height; y++) {
        row = bytes + (size_t)y * row_bytes;
        if (bits == 16) {
            for (x = 0; x < width; x++) {
                uint16_t s = t->le ? (uint16_t)(row[x * 2] | (row[x * 2 + 1] << 8))
                                   : (uint16_t)((row[x * 2] << 8) | row[x * 2 + 1]);
                dst[(size_t)y * width + x] = norm_sample(ifd, origin_x + x, origin_y + y, (float)s);
            }
        } else if (bits == 8) {
            for (x = 0; x < width; x++) {
                dst[(size_t)y * width + x] = norm_sample(ifd, origin_x + x, origin_y + y, (float)row[x]);
            }
        } else {
            uint32_t acc = 0;
            int acc_bits = 0;
            size_t bi = 0;
            for (x = 0; x < width; x++) {
                uint32_t mask = (1u << bits) - 1u;
                uint32_t sample;
                while (acc_bits < (int)bits) {
                    if (bi >= row_bytes) {
                        set_error("CinemaDNG packed row is truncated");
                        return -1;
                    }
                    acc = (acc << 8) | row[bi++];
                    acc_bits += 8;
                }
                acc_bits -= (int)bits;
                sample = (acc >> acc_bits) & mask;
                dst[(size_t)y * width + x] = norm_sample(ifd, origin_x + x, origin_y + y, (float)sample);
            }
        }
    }
    return 0;
}

static int decode_raw_samples(const Tiff *t, const RawIfd *ifd, float *full) {
    uint32_t width = ifd->width;
    uint32_t height = ifd->height;
    uint32_t i;
    if (!ifd->offsets || !ifd->counts || ifd->nstrips == 0) {
        set_error("CinemaDNG frame has no strips or tiles");
        return -1;
    }
    if (ifd->compression != 1 && ifd->compression != 7) {
        set_error("CinemaDNG compression is not uncompressed or lossless JPEG");
        return -1;
    }
    if (ifd->tiled) {
        uint32_t tw = ifd->tile_w ? ifd->tile_w : width;
        uint32_t th = ifd->tile_h ? ifd->tile_h : height;
        uint32_t tiles_x = (width + tw - 1) / tw;
        if (tw == 0 || th == 0) {
            set_error("CinemaDNG tile size is zero");
            return -1;
        }
        for (i = 0; i < ifd->nstrips; i++) {
            uint32_t tx = i % tiles_x;
            uint32_t ty = i / tiles_x;
            uint32_t ox = tx * tw;
            uint32_t oy = ty * th;
            uint32_t dw = width - ox;
            uint32_t dh = height - oy;
            const uint8_t *bytes;
            if (dw > tw) {
                dw = tw;
            }
            if (dh > th) {
                dh = th;
            }
            if (!in_range(t, ifd->offsets[i], ifd->counts[i])) {
                set_error("CinemaDNG tile is outside the file");
                return -1;
            }
            bytes = t->data + ifd->offsets[i];
            if (ifd->compression == 1) {
                float *tmp = (float *)malloc((size_t)tw * th * sizeof(float));
                uint32_t yy;
                if (!tmp) {
                    set_error("Out of memory decoding CinemaDNG");
                    return -1;
                }
                if (decode_uncompressed(t, ifd, bytes, ifd->counts[i], tmp, ox, oy, tw, th) != 0) {
                    free(tmp);
                    return -1;
                }
                for (yy = 0; yy < dh; yy++) {
                    memcpy(full + ((size_t)oy + yy) * width + ox, tmp + (size_t)yy * tw, dw * sizeof(float));
                }
                free(tmp);
            } else if (decode_ljpeg(
                           bytes, ifd->counts[i], full + (size_t)oy * width + ox, width, ifd, ox, oy, dw, dh
                       ) != 0) {
                return -1;
            }
        }
        return 0;
    }
    {
        uint32_t rps = ifd->rows_per_strip ? ifd->rows_per_strip : height;
        uint32_t row = 0;
        for (i = 0; i < ifd->nstrips; i++) {
            uint32_t rows = rps;
            const uint8_t *bytes;
            if (row >= height) {
                break;
            }
            if (rows > height - row) {
                rows = height - row;
            }
            if (!in_range(t, ifd->offsets[i], ifd->counts[i])) {
                set_error("CinemaDNG strip is outside the file");
                return -1;
            }
            bytes = t->data + ifd->offsets[i];
            if (ifd->compression == 1) {
                if (decode_uncompressed(t, ifd, bytes, ifd->counts[i], full + (size_t)row * width, 0, row, width, rows) !=
                    0) {
                    return -1;
                }
            } else if (decode_ljpeg(bytes, ifd->counts[i], full + (size_t)row * width, width, ifd, 0, row, width, rows) !=
                       0) {
                return -1;
            }
            row += rows;
        }
    }
    return 0;
}

static int score_ifd(const RawIfd *r) {
    if (r->width == 0 || r->height == 0 || r->samples != 1) {
        return -1;
    }
    if (r->compression != 1 && r->compression != 7) {
        return -1;
    }
    if (r->photometric == CDNG_CFA) {
        return 1000000 + (int)(r->width < 100000 && r->height < 100000 ? r->width * r->height / 64 : 0);
    }
    return -1;
}

static void fill_meta(const RawIfd *r, cinemadng_meta *meta, uint32_t left, uint32_t top, uint32_t cw, uint32_t ch) {
    int y;
    int x;
    memset(meta, 0, sizeof(*meta));
    meta->width = cw;
    meta->height = ch;
    meta->bits = r->bits;
    meta->compression = r->compression;
    for (y = 0; y < 2; y++) {
        for (x = 0; x < 2; x++) {
            meta->cfa[y * 2 + x] = r->cfa[(((top + (uint32_t)y) & 1u) * 2u) + ((left + (uint32_t)x) & 1u)];
        }
    }
    meta->has_forward = r->has_fm;
    meta->has_color_matrix = r->has_cm;
    meta->illuminant = r->illuminant;
    meta->orientation = r->orientation ? (int32_t)r->orientation : 1;
    meta->has_neutral = r->has_neutral;
    memcpy(meta->black, r->black, sizeof meta->black);
    meta->white = r->has_white ? r->white : (double)((1u << (r->bits ? r->bits : 16)) - 1u);
    memcpy(meta->neutral, r->neutral, sizeof meta->neutral);
    memcpy(meta->color_matrix, r->cm, sizeof meta->color_matrix);
    memcpy(meta->forward_matrix, r->fm, sizeof meta->forward_matrix);
    meta->baseline_exposure = r->baseline;
    meta->frame_rate = r->fps;
    memcpy(meta->make, r->make, sizeof meta->make);
    memcpy(meta->model, r->model, sizeof meta->model);
    memcpy(meta->unique_model, r->unique, sizeof meta->unique_model);
}

static int crop_rect(const RawIfd *r, uint32_t *left, uint32_t *top, uint32_t *cw, uint32_t *ch) {
    uint32_t l = 0;
    uint32_t t = 0;
    uint32_t rgt = r->width;
    uint32_t bot = r->height;
    if (r->has_active) {
        uint32_t at = r->active[0];
        uint32_t al = r->active[1];
        uint32_t ab = r->active[2];
        uint32_t ar = r->active[3];
        if (ab > at && ar > al && ar <= r->width && ab <= r->height) {
            t = at;
            l = al;
            bot = ab;
            rgt = ar;
        }
    }
    if (r->has_crop == 2 && r->crop_size[0] >= 1.0 && r->crop_size[1] >= 1.0) {
        uint32_t cx = l + (uint32_t)(r->crop_origin[0] + 0.5);
        uint32_t cy = t + (uint32_t)(r->crop_origin[1] + 0.5);
        uint32_t w = (uint32_t)(r->crop_size[0] + 0.5);
        uint32_t h = (uint32_t)(r->crop_size[1] + 0.5);
        if (w > 0 && h > 0 && cx >= l && cy >= t && cx + w <= rgt && cy + h <= bot) {
            l = cx;
            t = cy;
            rgt = cx + w;
            bot = cy + h;
        }
    }
    if (rgt <= l || bot <= t) {
        return -1;
    }
    *left = l;
    *top = t;
    *cw = rgt - l;
    *ch = bot - t;
    return 0;
}

static FILE *open_path(const char *path) {
#if defined(_WIN32)
    int n = MultiByteToWideChar(CP_UTF8, 0, path, -1, NULL, 0);
    wchar_t *w;
    FILE *f;
    if (n <= 0) {
        return NULL;
    }
    w = (wchar_t *)malloc((size_t)n * sizeof(wchar_t));
    if (!w) {
        return NULL;
    }
    MultiByteToWideChar(CP_UTF8, 0, path, -1, w, n);
    f = _wfopen(w, L"rb");
    free(w);
    return f;
#else
    return fopen(path, "rb");
#endif
}

typedef struct {
    uint8_t *buf;
    Tiff t;
    RawIfd ifds[CDNG_MAX_IFD];
    int nifd;
    int best;
} Loaded;

static void loaded_clear(Loaded *L) {
    memset(L, 0, sizeof(*L));
    L->best = -1;
}

static void loaded_free(Loaded *L) {
    int i;
    if (!L) {
        return;
    }
    for (i = 0; i < L->nifd; i++) {
        raw_free(&L->ifds[i]);
    }
    free(L->buf);
    L->buf = NULL;
    L->nifd = 0;
}

static int load_frame(const char *path, Loaded *L) {
    FILE *fp;
    long sz;
    uint32_t sub_offs[32];
    int nsub = 0;
    int queued = 0;
    int i;
    int best_score = -1;
    loaded_clear(L);
    fp = open_path(path);
    if (!fp) {
        set_error("Could not open CinemaDNG frame");
        return -1;
    }
    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        set_error("Could not read CinemaDNG frame");
        return -1;
    }
    sz = ftell(fp);
    if (sz < 16 || (unsigned long)sz > CDNG_MAX_BYTES) {
        fclose(fp);
        set_error("CinemaDNG frame size is not supported");
        return -1;
    }
    rewind(fp);
    L->buf = (uint8_t *)malloc((size_t)sz);
    if (!L->buf || fread(L->buf, 1, (size_t)sz, fp) != (size_t)sz) {
        fclose(fp);
        set_error("Could not read CinemaDNG frame");
        return -1;
    }
    fclose(fp);
    if (!((L->buf[0] == 'I' && L->buf[1] == 'I') || (L->buf[0] == 'M' && L->buf[1] == 'M'))) {
        set_error("Not a TIFF or CinemaDNG frame");
        return -1;
    }
    L->t.data = L->buf;
    L->t.size = (size_t)sz;
    L->t.le = L->buf[0] == 'I';
    if (ru16(&L->t, 2) != 42) {
        set_error("Not a classic TIFF CinemaDNG frame");
        return -1;
    }
    sub_offs[nsub++] = ru32(&L->t, 4);
    while (queued < nsub && L->nifd < CDNG_MAX_IFD) {
        uint32_t off = sub_offs[queued++];
        if (parse_ifd(&L->t, off, &L->ifds[L->nifd], sub_offs, &nsub, 32) != 0) {
            raw_free(&L->ifds[L->nifd]);
            return -1;
        }
        L->nifd++;
    }
    for (i = 0; i < L->nifd; i++) {
        int sc = score_ifd(&L->ifds[i]);
        if (sc > best_score) {
            best_score = sc;
            L->best = i;
        }
    }
    if (L->best < 0) {
        set_error("No CinemaDNG Bayer frame in this file");
        return -1;
    }
    if (L->ifds[L->best].bits == 0 || L->ifds[L->best].bits > 16) {
        set_error("CinemaDNG bit depth is not supported");
        return -1;
    }
    return 0;
}

static int cropped_size(const RawIfd *r, uint32_t *left, uint32_t *top, uint32_t *cw, uint32_t *ch) {
    if (crop_rect(r, left, top, cw, ch) != 0) {
        set_error("CinemaDNG crop is empty");
        return -1;
    }
    return 0;
}

CDNG_API int cinemadng_probe(const char *path, cinemadng_meta *meta) {
    Loaded L;
    uint32_t left = 0;
    uint32_t top = 0;
    uint32_t cw = 0;
    uint32_t ch = 0;
    g_err[0] = 0;
    loaded_clear(&L);
    if (!path || !meta) {
        set_error("CinemaDNG probe arguments are missing");
        return -1;
    }
    if (load_frame(path, &L) != 0) {
        loaded_free(&L);
        return -1;
    }
    if (cropped_size(&L.ifds[L.best], &left, &top, &cw, &ch) != 0) {
        loaded_free(&L);
        return -1;
    }
    fill_meta(&L.ifds[L.best], meta, left, top, cw, ch);
    loaded_free(&L);
    return 0;
}

CDNG_API float *cinemadng_decode(const char *path, cinemadng_meta *meta) {
    Loaded L;
    float *full = NULL;
    float *cropped = NULL;
    uint32_t left = 0;
    uint32_t top = 0;
    uint32_t cw = 0;
    uint32_t ch = 0;
    int i;
    g_err[0] = 0;
    loaded_clear(&L);
    if (!path || !meta) {
        set_error("CinemaDNG decode arguments are missing");
        return NULL;
    }
    if (load_frame(path, &L) != 0) {
        loaded_free(&L);
        return NULL;
    }
    if ((uint64_t)L.ifds[L.best].width * (uint64_t)L.ifds[L.best].height > 80000000ull) {
        set_error("CinemaDNG frame is too large");
        loaded_free(&L);
        return NULL;
    }
    full = (float *)calloc((size_t)L.ifds[L.best].width * L.ifds[L.best].height, sizeof(float));
    if (!full) {
        set_error("Out of memory decoding CinemaDNG");
        loaded_free(&L);
        return NULL;
    }
    if (decode_raw_samples(&L.t, &L.ifds[L.best], full) != 0) {
        free(full);
        loaded_free(&L);
        return NULL;
    }
    if (cropped_size(&L.ifds[L.best], &left, &top, &cw, &ch) != 0) {
        free(full);
        loaded_free(&L);
        return NULL;
    }
    cropped = (float *)malloc((size_t)cw * ch * sizeof(float));
    if (!cropped) {
        set_error("Out of memory decoding CinemaDNG");
        free(full);
        loaded_free(&L);
        return NULL;
    }
    for (i = 0; i < (int)ch; i++) {
        memcpy(
            cropped + (size_t)i * cw,
            full + ((size_t)top + (uint32_t)i) * L.ifds[L.best].width + left,
            cw * sizeof(float)
        );
    }
    fill_meta(&L.ifds[L.best], meta, left, top, cw, ch);
    free(full);
    loaded_free(&L);
    return cropped;
}
