import CoreGraphics
import Darwin
import Foundation

/// Viewer adjustments, as in the player: gain is a scene-linear multiply
/// before the view, gamma is ``out = in^(1/gamma)`` after it.
struct ViewerLook: Equatable {
    var gain: Float = 1
    var gamma: Float = 1
}

/// A cached scene-linear frame, stored log2-encoded in 16 bits per channel.
///
/// Each channel is ``(log2(x) - minExp) / (maxExp - minExp)``, the same
/// shaper the view cube expects, kept unclamped over ``low…high`` so exposure
/// can lift shadows or pull highlights without decoding the file again. At
/// 16 bits a step is well under a thousandth of a stop. A 960×540 frame is
/// about 4 MB.
struct SceneFrame {
    static let low: Float = -0.5
    static let high: Float = 1.5

    let width: Int
    let height: Int
    let minExp: Float
    let maxExp: Float
    /// RGBA, ``width × height × 4``.
    let codes: [UInt16]

    var byteCount: Int { codes.count * MemoryLayout<UInt16>.size }

    static func encode(_ linear: LinearRGBA, minExp: Float, maxExp: Float) -> SceneFrame {
        let count = linear.width * linear.height
        let span = maxExp - minExp
        let range = high - low
        var codes = [UInt16](repeating: 0, count: count * 4)
        func code(_ value: Float) -> UInt16 {
            var e = low
            if value > 0, span > 0 {
                e = (log2(value) - minExp) / span
            }
            let unit = (min(max(e, low), high) - low) / range
            return UInt16((unit * 65535).rounded())
        }
        linear.channels.withUnsafeBufferPointer { src in
            codes.withUnsafeMutableBufferPointer { dst in
                for i in 0..<count {
                    let base = i * 4
                    dst[base] = code(src[base])
                    dst[base + 1] = code(src[base + 1])
                    dst[base + 2] = code(src[base + 2])
                    dst[base + 3] = UInt16((min(max(src[base + 3], 0), 1) * 65535).rounded())
                }
            }
        }
        return SceneFrame(width: linear.width, height: linear.height, minExp: minExp, maxExp: maxExp, codes: codes)
    }

    /// Shaped values for ``lut`` at this ``gain``: the cube's 0…1 domain.
    func shaped(for lut: ViewLUT, gain: Float) -> [Float] {
        let span = maxExp - minExp
        let lutSpan = lut.maxExp - lut.minExp
        let range = Self.high - Self.low
        // e_lut = (log2(x · gain) - lutMin) / lutSpan, with log2(x) = e · span + minExp.
        let scale = range * span / max(lutSpan, 1e-6) / 65535
        let bias = (Self.low * span + minExp + log2(max(gain, 1e-6)) - lut.minExp) / max(lutSpan, 1e-6)
        let floorCode = UInt16(0)
        var out = [Float](repeating: 0, count: codes.count)
        codes.withUnsafeBufferPointer { src in
            out.withUnsafeMutableBufferPointer { dst in
                let count = width * height
                for i in 0..<count {
                    let base = i * 4
                    for c in 0..<3 {
                        let raw = src[base + c]
                        // The bottom code is "zero or below", which stays black.
                        let value = raw == floorCode ? 0 : Float(raw) * scale + bias
                        dst[base + c] = min(max(value, 0), 1)
                    }
                    dst[base + 3] = Float(src[base + 3]) / 65535
                }
            }
        }
        return out
    }

    /// 8-bit preview through ``choice``, tagged with its display color space.
    /// Without a cube, the filmic fallback in sRGB.
    func render(choice: ViewChoice?, look: ViewerLook) -> CGImage? {
        if let choice {
            let domain = self.shaped(for: choice.lut, gain: look.gain)
            return ColorCube.applyShaped(
                domain,
                width: width,
                height: height,
                lut: choice.lut,
                gamma: look.gamma,
                outputSpace: choice.colorSpace
            )
        }
        return renderFilmic(look: look)
    }

    private func renderFilmic(look: ViewerLook) -> CGImage? {
        let count = width * height
        let span = maxExp - minExp
        let range = Self.high - Self.low
        let power = 1 / max(look.gamma, 1e-3)
        var bytes = [UInt8](repeating: 0, count: count * 4)
        for i in 0..<count {
            let base = i * 4
            for c in 0..<3 {
                let raw = codes[base + c]
                var unit: Float = 0
                if raw > 0 {
                    let e = Float(raw) / 65535 * range + Self.low
                    let linear = exp2(e * span + minExp) * look.gain
                    unit = powf(ToneMap.acesFilmic(linear), power)
                }
                bytes[base + c] = DPXDecoder.quantize(unit)
            }
            bytes[base + 3] = UInt8(codes[base + 3] >> 8)
        }
        return try? imageFromRGBA(bytes, width: width, height: height)
    }
}
