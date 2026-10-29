import CoreGraphics
import Darwin
import Foundation

/// ``view.lut`` written by ``src/core/quicklook_lut.py``.
///
/// Little-endian header, then UTF-8 names, then an RGBA float cube (red
/// fastest, then green, then blue). Bit 0 of the flags selects the analytic
/// log2 shaper. The appex prefers the App Group file, then the Application
/// Support fallback, then the copy baked into the appex at build time.
struct ViewLUT {
    let cubeSize: Int
    let minExp: Float
    let maxExp: Float
    let sourceName: String
    let displayName: String
    let viewName: String
    /// Length ``cubeSize³ × 4``.
    let samples: [Float]

    var cubeData: Data {
        samples.withUnsafeBufferPointer { buffer in
            Data(buffer: buffer)
        }
    }

    /// Scene-linear channel → 0…1 cube coordinate. Non-positive values sit on
    /// the first lattice entry, which was baked at ``2^minExp``.
    static func shaper(_ value: Float, minExp: Float, maxExp: Float) -> Float {
        if !(value > 0) {
            return 0
        }
        let span = maxExp - minExp
        if !(span > 0) {
            return 0
        }
        let floor = exp2(minExp)
        let encoded = (log2(max(value, floor)) - minExp) / span
        return min(max(encoded, 0), 1)
    }

    static func parse(_ data: Data) -> ViewLUT? {
        if data.count < 28 {
            return nil
        }
        if data.subdata(in: 0..<4) != Data("EXQL".utf8) {
            return nil
        }
        let version = readU16(data, 4)
        let cubeSize = Int(readU16(data, 6))
        let flags = readU16(data, 8)
        if version != 1 || cubeSize < 2 || cubeSize > 65 || flags & 1 == 0 {
            return nil
        }
        let minExp = readF32(data, 12)
        let maxExp = readF32(data, 16)
        if !(maxExp > minExp) {
            return nil
        }
        let srcLen = Int(readU16(data, 20))
        let displayLen = Int(readU16(data, 22))
        let viewLen = Int(readU16(data, 24))
        let nameStart = 28
        let nameEnd = nameStart + srcLen + displayLen + viewLen
        let floatCount = cubeSize * cubeSize * cubeSize * 4
        if nameEnd > data.count || data.count < nameEnd + floatCount * 4 {
            return nil
        }
        let names = data.subdata(in: nameStart..<nameEnd)
        guard
            let source = String(data: names.subdata(in: 0..<srcLen), encoding: .utf8),
            let display = String(data: names.subdata(in: srcLen..<(srcLen + displayLen)), encoding: .utf8),
            let view = String(data: names.subdata(in: (srcLen + displayLen)..<(srcLen + displayLen + viewLen)), encoding: .utf8)
        else {
            return nil
        }
        var samples = [Float](repeating: 0, count: floatCount)
        let cubeBytes = floatCount * 4
        data.withUnsafeBytes { source in
            samples.withUnsafeMutableBytes { destination in
                guard
                    let from = source.baseAddress?.advanced(by: nameEnd),
                    let to = destination.baseAddress
                else {
                    return
                }
                to.copyMemory(from: from, byteCount: cubeBytes)
            }
        }
        return ViewLUT(
            cubeSize: cubeSize,
            minExp: minExp,
            maxExp: maxExp,
            sourceName: source,
            displayName: display,
            viewName: view,
            samples: samples
        )
    }

    func sample(r: Float, g: Float, b: Float) -> (Float, Float, Float) {
        let scale = Float(cubeSize - 1)
        let er = Self.shaper(r, minExp: minExp, maxExp: maxExp) * scale
        let eg = Self.shaper(g, minExp: minExp, maxExp: maxExp) * scale
        let eb = Self.shaper(b, minExp: minExp, maxExp: maxExp) * scale
        return trilinear(er, eg, eb)
    }

    private func trilinear(_ r: Float, _ g: Float, _ b: Float) -> (Float, Float, Float) {
        let last = cubeSize - 1
        func axis(_ t: Float) -> (Int, Int, Float) {
            let clamped = min(max(t, 0), Float(last))
            let low = min(Int(clamped), last)
            let high = min(low + 1, last)
            return (low, high, clamped - Float(low))
        }
        let (r0, r1, fr) = axis(r)
        let (g0, g1, fg) = axis(g)
        let (b0, b1, fb) = axis(b)
        func at(_ red: Int, _ green: Int, _ blue: Int) -> (Float, Float, Float) {
            let index = ((blue * cubeSize + green) * cubeSize + red) * 4
            return (samples[index], samples[index + 1], samples[index + 2])
        }
        func lerp(_ a: (Float, Float, Float), _ b: (Float, Float, Float), _ t: Float) -> (Float, Float, Float) {
            (a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t, a.2 + (b.2 - a.2) * t)
        }
        let c00 = lerp(at(r0, g0, b0), at(r1, g0, b0), fr)
        let c01 = lerp(at(r0, g0, b1), at(r1, g0, b1), fr)
        let c10 = lerp(at(r0, g1, b0), at(r1, g1, b0), fr)
        let c11 = lerp(at(r0, g1, b1), at(r1, g1, b1), fr)
        let c0 = lerp(c00, c10, fg)
        let c1 = lerp(c01, c11, fg)
        return lerp(c0, c1, fb)
    }
}

/// A view the preview can show: one baked cube and the Core Graphics color
/// space its 8-bit output is encoded in.
struct ViewChoice {
    let lut: ViewLUT
    /// ``displayP3`` or ``sRGB``.
    let cgSpace: String

    var isDisplayP3: Bool { cgSpace == "displayP3" }

    var title: String { "\(lut.displayName)  ·  \(lut.viewName)" }

    var colorSpace: CGColorSpace {
        if isDisplayP3, let p3 = CGColorSpace(name: CGColorSpace.displayP3) {
            return p3
        }
        return CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
    }
}

/// ``views.lut``: every SDR view of the sRGB and Display P3 displays.
///
/// ``EXQB``, u16 version, u16 count, u16 Display P3 default, u16 sRGB default
/// (0xFFFF when absent), then per entry a u16-prefixed CG space tag and a
/// u32-prefixed ``view.lut`` blob.
struct ViewLUTBundle {
    let choices: [ViewChoice]
    let p3Default: Int?
    let srgbDefault: Int?

    /// Display P3 on a P3 screen (every recent Mac), sRGB otherwise.
    func defaultIndex(preferP3: Bool) -> Int {
        if preferP3, let p3Default { return p3Default }
        if let srgbDefault { return srgbDefault }
        if let p3Default { return p3Default }
        return 0
    }

    static func parse(_ data: Data) -> ViewLUTBundle? {
        if data.count < 12 || data.subdata(in: 0..<4) != Data("EXQB".utf8) {
            return nil
        }
        let version = readU16(data, 4)
        let count = Int(readU16(data, 6))
        let p3 = Int(readU16(data, 8))
        let srgb = Int(readU16(data, 10))
        if version != 1 || count == 0 {
            return nil
        }
        var offset = 12
        var choices: [ViewChoice] = []
        for _ in 0..<count {
            guard offset + 2 <= data.count else { return nil }
            let tagLen = Int(readU16(data, offset))
            offset += 2
            guard offset + tagLen + 4 <= data.count else { return nil }
            let tag = String(decoding: data.subdata(in: offset..<(offset + tagLen)), as: UTF8.self)
            offset += tagLen
            let blobLen = Int(readU32(data, offset))
            offset += 4
            guard offset + blobLen <= data.count,
                let lut = ViewLUT.parse(data.subdata(in: offset..<(offset + blobLen)))
            else {
                return nil
            }
            offset += blobLen
            choices.append(ViewChoice(lut: lut, cgSpace: tag))
        }
        return ViewLUTBundle(
            choices: choices,
            p3Default: p3 < count ? p3 : nil,
            srgbDefault: srgb < count ? srgb : nil
        )
    }
}

/// Resolved once per process. Quick Look's lifetime is one preview.
enum ViewLUTStore {
    private static let lock = NSLock()
    private static var resolved = false
    private static var cached: ViewLUT?
    private static var bundleResolved = false
    private static var cachedBundle: ViewLUTBundle?

    static func preferred() -> ViewLUT? {
        lock.lock()
        defer { lock.unlock() }
        if resolved {
            return cached
        }
        resolved = true
        if let data = SharedFile.data(named: "view.lut") {
            cached = ViewLUT.parse(data)
        }
        return cached
    }

    /// The view menu. Falls back to the single default cube, tagged sRGB.
    static func bundle() -> ViewLUTBundle? {
        lock.lock()
        if !bundleResolved {
            bundleResolved = true
            if let data = SharedFile.data(named: "views.lut") {
                cachedBundle = ViewLUTBundle.parse(data)
            }
        }
        let found = cachedBundle
        lock.unlock()
        if let found {
            return found
        }
        guard let single = preferred() else { return nil }
        return ViewLUTBundle(
            choices: [ViewChoice(lut: single, cgSpace: "sRGB")], p3Default: nil, srgbDefault: 0)
    }
}

/// Files the app bakes for the extension: the App Group container first, then
/// ``~/Library/Application Support/EXR Converter``, then the copy baked into
/// the appex at build time.
enum SharedFile {
    static func data(named name: String) -> Data? {
        if let container = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: QuickLookPreferences.appGroupID),
            let data = try? Data(contentsOf: container.appendingPathComponent(name))
        {
            return data
        }
        if let home = RealHome.url(),
            let data = try? Data(
                contentsOf: home.appendingPathComponent("Library/Application Support/EXR Converter/\(name)"))
        {
            return data
        }
        let url = Bundle.main.resourceURL?.appendingPathComponent(name)
        if let url, let data = try? Data(contentsOf: url) {
            return data
        }
        return nil
    }
}

enum RealHome {
    /// Sandbox ``homeDirectoryForCurrentUser`` is the appex container. The
    /// temporary-exception paths are relative to this directory.
    static func url() -> URL? {
        guard let pw = getpwuid(getuid()), let dir = pw.pointee.pw_dir else {
            return nil
        }
        return URL(fileURLWithPath: String(cString: dir), isDirectory: true)
    }
}

private func readU16(_ data: Data, _ offset: Int) -> UInt16 {
    UInt16(data[offset]) | (UInt16(data[offset + 1]) << 8)
}

private func readU32(_ data: Data, _ offset: Int) -> UInt32 {
    UInt32(data[offset])
        | (UInt32(data[offset + 1]) << 8)
        | (UInt32(data[offset + 2]) << 16)
        | (UInt32(data[offset + 3]) << 24)
}

private func readF32(_ data: Data, _ offset: Int) -> Float {
    let bits = UInt32(data[offset])
        | (UInt32(data[offset + 1]) << 8)
        | (UInt32(data[offset + 2]) << 16)
        | (UInt32(data[offset + 3]) << 24)
    return Float(bitPattern: bits)
}
