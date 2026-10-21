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

/// Resolved once per process. Quick Look's lifetime is one preview.
enum ViewLUTStore {
    private static let lock = NSLock()
    private static var resolved = false
    private static var cached: ViewLUT?

    static func preferred() -> ViewLUT? {
        lock.lock()
        defer { lock.unlock() }
        if resolved {
            return cached
        }
        resolved = true
        cached = load()
        return cached
    }

    private static func load() -> ViewLUT? {
        if let data = readGroup(), let lut = ViewLUT.parse(data) {
            return lut
        }
        if let data = readFallback(), let lut = ViewLUT.parse(data) {
            return lut
        }
        if let url = Bundle.main.url(forResource: "view", withExtension: "lut"),
            let data = try? Data(contentsOf: url),
            let lut = ViewLUT.parse(data)
        {
            return lut
        }
        return nil
    }

    private static func readGroup() -> Data? {
        guard
            let container = FileManager.default.containerURL(
                forSecurityApplicationGroupIdentifier: QuickLookPreferences.appGroupID)
        else {
            return nil
        }
        return try? Data(contentsOf: container.appendingPathComponent("view.lut"))
    }

    private static func readFallback() -> Data? {
        guard let home = RealHome.url() else { return nil }
        let url = home.appendingPathComponent("Library/Application Support/EXR Converter/view.lut")
        return try? Data(contentsOf: url)
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

private func readF32(_ data: Data, _ offset: Int) -> Float {
    let bits = UInt32(data[offset])
        | (UInt32(data[offset + 1]) << 8)
        | (UInt32(data[offset + 2]) << 16)
        | (UInt32(data[offset + 3]) << 24)
    return Float(bitPattern: bits)
}
