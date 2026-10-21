import Darwin
import Foundation

/// Display approximation for scene-linear pictures.
///
/// The app's EXR → video default is OCIO ``scene_linear`` → ``Output - Rec.709``
/// on the bundled ACES config. Linking OpenColorIO into a Quick Look extension
/// would add tens of megabytes, so this is the Narkowicz 2015 ACES filmic fit:
/// a curve that expects linear Rec.709 / sRGB primaries and returns encoded
/// sRGB. It is not the ACES Output Transform. ImageIO's OpenEXR reader supplies
/// the linear RGB (typically extended linear sRGB). ACEScg plates will be close
/// enough to recognise, not a color-critical match to the app.
enum ToneMap {
    static func acesFilmic(_ x: Float) -> Float {
        let clamped = max(x, 0)
        let a: Float = 2.51
        let b: Float = 0.03
        let c: Float = 2.43
        let d: Float = 0.59
        let e: Float = 0.14
        let numerator = clamped * (a * clamped + b)
        let denominator = clamped * (c * clamped + d) + e
        if denominator == 0 {
            return 0
        }
        return min(max(numerator / denominator, 0), 1)
    }

    /// Kodak Cineon / DPX printing-density preview.
    ///
    /// 10-bit reference white code 685 maps to linear 1.0
    /// (``10^((cv - 685) * 0.002 / 0.6)``). Other bit depths are scaled onto
    /// that 10-bit lattice first. This is the usual soft conversion, not a
    /// scanned negative's measured density.
    static func cineonToLinear(code: Float, bitDepth: Int) -> Float {
        let bits = min(max(bitDepth, 1), 16)
        let maxCode = Float((1 << bits) - 1)
        if maxCode <= 0 {
            return 0
        }
        let cv10 = code / maxCode * 1023
        return powf(10, (cv10 - 685) * 0.002 / 0.6)
    }

    static func normalizedCode(_ code: Float, bitDepth: Int) -> Float {
        let bits = min(max(bitDepth, 1), 16)
        let maxCode = Float((1 << bits) - 1)
        if maxCode <= 0 {
            return 0
        }
        return min(max(code / maxCode, 0), 1)
    }
}
