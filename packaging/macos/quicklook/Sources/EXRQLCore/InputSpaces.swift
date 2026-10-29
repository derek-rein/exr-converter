import Foundation

/// One scene color space from ``inputs.json`` (``src/core/quicklook_lut.py``).
/// Pixels go through ``curve`` per channel (log and video spaces), then the
/// row-major 3×3 ``matrix``, which lands them in the cube's source space
/// (``scene_linear``, ACEScg on the bundled config).
struct InputSpace {
    let name: String
    let aliases: [String]
    let interop: String
    let matrix: [Float]
    let curve: [Float]?
    let curveMin: Float
    let curveMax: Float

    var isIdentity: Bool {
        guard curve == nil, matrix.count == 9 else { return false }
        let identity: [Float] = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        return zip(matrix, identity).allSatisfy { abs($0 - $1) < 1e-5 }
    }

    func matches(_ query: String) -> Bool {
        let key = query.lowercased()
        if name.lowercased() == key || (!interop.isEmpty && interop.lowercased() == key) {
            return true
        }
        return aliases.contains { $0.lowercased() == key }
    }

    func linearize(_ code: Float) -> Float {
        guard let curve, curve.count > 1 else { return code }
        let span = curveMax - curveMin
        guard span > 0 else { return code }
        let last = curve.count - 1
        let t = (code - curveMin) / span * Float(last)
        if !(t > 0) {
            // Extend the first segment so codes below the table stay monotonic.
            let slope = (curve[1] - curve[0])
            return curve[0] + slope * t
        }
        if t >= Float(last) {
            let slope = (curve[last] - curve[last - 1])
            return curve[last] + slope * (t - Float(last))
        }
        let low = Int(t)
        let frac = t - Float(low)
        return curve[low] + (curve[low + 1] - curve[low]) * frac
    }

    /// Straight RGBA in place. Alpha is left alone.
    func apply(to channels: inout [Float]) {
        if isIdentity { return }
        let m = matrix
        let count = channels.count / 4
        for i in 0..<count {
            let base = i * 4
            let r = linearize(channels[base])
            let g = linearize(channels[base + 1])
            let b = linearize(channels[base + 2])
            channels[base] = m[0] * r + m[1] * g + m[2] * b
            channels[base + 1] = m[3] * r + m[4] * g + m[5] * b
            channels[base + 2] = m[6] * r + m[7] * g + m[8] * b
        }
    }
}

struct InputCatalog {
    let target: String
    let spaces: [InputSpace]

    func lookup(_ query: String) -> InputSpace? {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return nil }
        return spaces.first { $0.matches(trimmed) }
    }

    static func parse(_ data: Data) -> InputCatalog? {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let root = object as? [String: Any],
            (root["version"] as? NSNumber)?.intValue == 1,
            let target = root["target"] as? String,
            let raw = root["spaces"] as? [[String: Any]]
        else {
            return nil
        }
        let curveMin = (root["curve_min"] as? NSNumber)?.floatValue ?? -0.125
        let curveMax = (root["curve_max"] as? NSNumber)?.floatValue ?? 1.25
        var spaces: [InputSpace] = []
        for entry in raw {
            guard
                let name = entry["name"] as? String,
                let matrix = (entry["matrix"] as? [NSNumber])?.map({ $0.floatValue }),
                matrix.count == 9
            else {
                continue
            }
            let curve = (entry["curve"] as? [NSNumber])?.map { $0.floatValue }
            spaces.append(
                InputSpace(
                    name: name,
                    aliases: entry["aliases"] as? [String] ?? [],
                    interop: entry["interop"] as? String ?? "",
                    matrix: matrix,
                    curve: (curve?.count ?? 0) > 1 ? curve : nil,
                    curveMin: curveMin,
                    curveMax: curveMax
                ))
        }
        return InputCatalog(target: target, spaces: spaces)
    }
}

enum InputCatalogStore {
    private static let lock = NSLock()
    private static var resolved = false
    private static var cached: InputCatalog?

    static func shared() -> InputCatalog? {
        lock.lock()
        defer { lock.unlock() }
        if !resolved {
            resolved = true
            if let data = SharedFile.data(named: "inputs.json") {
                cached = InputCatalog.parse(data)
            }
        }
        return cached
    }
}

/// What the preview decided the stored pixels are, and why.
struct InputDetection {
    let space: InputSpace?
    /// The color space name shown in the preview.
    let name: String
    /// Where it came from: an EXR attribute name, ``override`` or ``default``.
    let source: String
}

/// Same order as ``probe_pixel_colorspace_detail`` in ``src/core/sequence.py``.
enum InputDetector {
    static let knownChromaticities: [(String, [Float])] = [
        ("Linear Rec.709 (sRGB)", [0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290]),
        ("ACEScg", [0.713, 0.293, 0.165, 0.830, 0.128, 0.044, 0.32168, 0.33767]),
        ("ACES2065-1", [0.7347, 0.2653, 0.0, 1.0, 0.0001, -0.0770, 0.32168, 0.33767]),
        ("Linear P3-D65", [0.680, 0.320, 0.265, 0.690, 0.150, 0.060, 0.3127, 0.3290]),
        ("Linear Rec.2020", [0.708, 0.292, 0.170, 0.797, 0.131, 0.046, 0.3127, 0.3290]),
    ]

    static func name(forChromaticities xy: [Float]) -> String? {
        guard xy.count == 8 else { return nil }
        for (name, ref) in knownChromaticities
        where zip(xy, ref).allSatisfy({ abs($0 - $1) <= 0.002 }) {
            return name
        }
        return nil
    }

    static func detect(header: EXRHeader?, catalog: InputCatalog?) -> InputDetection {
        var candidates: [(String, String)] = []
        if let header {
            if let ours = header.strings["exrconverter:dstColorSpace"] {
                candidates.append((ours, "exrconverter:dstColorSpace"))
            }
            if let interop = header.strings["colorInteropID"] {
                candidates.append((interop, "colorInteropID"))
            }
            if let xy = header.chromaticities, let named = name(forChromaticities: xy) {
                candidates.append((named, "chromaticities"))
            }
            if let oiio = header.strings["oiio:ColorSpace"] {
                candidates.append((oiio, "oiio:ColorSpace"))
            }
        }
        if let catalog {
            for (name, source) in candidates {
                if let space = catalog.lookup(name) {
                    return InputDetection(space: space, name: space.name, source: source)
                }
            }
            let target = catalog.lookup(catalog.target)
            return InputDetection(space: target, name: catalog.target, source: "default")
        }
        return InputDetection(space: nil, name: "scene_linear", source: "default")
    }
}
