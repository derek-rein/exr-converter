import CoreGraphics
import Foundation

enum DPXError: Error, CustomStringConvertible {
    case notDPX
    case truncated
    case unsupported(String)

    var description: String {
        switch self {
        case .notDPX:
            return "Not a DPX file."
        case .truncated:
            return "DPX header or pixel data is truncated."
        case .unsupported(let reason):
            return reason
        }
    }
}

/// Minimal SMPTE 268M reader for uncompressed RGB DPX:
/// 8-bit and 16-bit tightly packed, and 10-bit samples filled to 32-bit words
/// (method A, packing 1). Printing-density and logarithmic headers use a Cineon
/// preview curve, then the same Rec.709 approximation as OpenEXR. Video
/// transfer values are treated as already display-encoded. User-defined and
/// linear headers are tone-mapped from full-range code values.
enum DPXDecoder {
    static func decode(url: URL, maxPixel: Int) throws -> CGImage {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let header = try read(handle, offset: 0, count: 832)
        guard header.count >= 832 else { throw DPXError.truncated }

        let littleEndian: Bool
        switch (header[0], header[1], header[2], header[3]) {
        case (UInt8(ascii: "S"), UInt8(ascii: "D"), UInt8(ascii: "P"), UInt8(ascii: "X")):
            littleEndian = false
        case (UInt8(ascii: "X"), UInt8(ascii: "P"), UInt8(ascii: "D"), UInt8(ascii: "S")):
            littleEndian = true
        default:
            throw DPXError.notDPX
        }

        let fileImageOffset = readU32(header, 4, littleEndian)
        let orientation = readU16(header, 768, littleEndian)
        let width = Int(readU32(header, 772, littleEndian))
        let height = Int(readU32(header, 776, littleEndian))
        if width <= 0 || height <= 0 || width > 16_384 || height > 16_384 {
            throw DPXError.unsupported("DPX dimensions \(width)×\(height) are outside the preview limit.")
        }

        let descriptor = header[800]
        let transfer = header[801]
        let bitDepth = Int(header[803])
        let packing = readU16(header, 804, littleEndian)
        let encoding = readU16(header, 806, littleEndian)
        let elementOffset = readU32(header, 808, littleEndian)
        let eolPadding = Int(readU32(header, 812, littleEndian))
        if encoding != 0 {
            throw DPXError.unsupported("Compressed DPX (encoding \(encoding)) is not previewed.")
        }
        guard let components = componentsPerPixel(descriptor) else {
            throw DPXError.unsupported(
                "DPX descriptor \(descriptor) is not RGB. Quick Look previews RGB and RGBA.")
        }
        guard let pixelBytes = bytesPerPixel(bitDepth: bitDepth, components: components, packing: packing)
        else {
            throw DPXError.unsupported(
                "DPX \(bitDepth)-bit packing \(packing) is not previewed. "
                    + "Supported: 8-bit and 16-bit RGB, and 10-bit filled method A.")
        }

        var dataOffset = elementOffset > 0 ? elementOffset : fileImageOffset
        if dataOffset == 0 {
            dataOffset = 8192
        }
        let rowStride = width * pixelBytes + max(eolPadding, 0)
        let limit = max(maxPixel, 1)
        let step = max(1, (max(width, height) + limit - 1) / limit)
        let outW = (width - 1) / step + 1
        let outH = (height - 1) / step + 1
        // SMPTE 268M: 3, 4, 7, and 8 store the first scan line at the bottom.
        let flipY = orientation == 3 || orientation == 4 || orientation == 7 || orientation == 8

        // Scene-linear and log DPX become a float plate so the OCIO cube (or the
        // filmic fallback) runs once. Video transfer is already display-encoded.
        if !isVideoTransfer(transfer) {
            var linear = [Float](repeating: 0, count: outW * outH * 4)
            for outY in 0..<outH {
                let srcY = flipY ? (height - 1 - outY * step) : outY * step
                let rowOffset = UInt64(dataOffset) + UInt64(srcY) * UInt64(rowStride)
                let row = try read(handle, offset: rowOffset, count: rowStride)
                if row.count < width * pixelBytes {
                    throw DPXError.truncated
                }
                for outX in 0..<outW {
                    let codes = sample(
                        row: row,
                        x: outX * step,
                        bitDepth: bitDepth,
                        components: components,
                        packing: packing,
                        littleEndian: littleEndian
                    )
                    let i = (outY * outW + outX) * 4
                    linear[i] = linearCode(codes.0, bitDepth: bitDepth, transfer: transfer)
                    linear[i + 1] = linearCode(codes.1, bitDepth: bitDepth, transfer: transfer)
                    linear[i + 2] = linearCode(codes.2, bitDepth: bitDepth, transfer: transfer)
                    linear[i + 3] = 1
                }
            }
            let image = try imageFromLinear(linear, width: outW, height: outH)
            return PreviewRenderer.toneMap(image) ?? image
        }

        var pixels = [UInt8](repeating: 0, count: outW * outH * 4)
        for outY in 0..<outH {
            let srcY = flipY ? (height - 1 - outY * step) : outY * step
            let rowOffset = UInt64(dataOffset) + UInt64(srcY) * UInt64(rowStride)
            let row = try read(handle, offset: rowOffset, count: rowStride)
            if row.count < width * pixelBytes {
                throw DPXError.truncated
            }
            for outX in 0..<outW {
                let codes = sample(
                    row: row,
                    x: outX * step,
                    bitDepth: bitDepth,
                    components: components,
                    packing: packing,
                    littleEndian: littleEndian
                )
                let i = (outY * outW + outX) * 4
                pixels[i] = quantize(normalizedCode(codes.0, bitDepth: bitDepth))
                pixels[i + 1] = quantize(normalizedCode(codes.1, bitDepth: bitDepth))
                pixels[i + 2] = quantize(normalizedCode(codes.2, bitDepth: bitDepth))
                pixels[i + 3] = 255
            }
        }
        return try imageFromRGBA(pixels, width: outW, height: outH)
    }

    static func isVideoTransfer(_ transfer: UInt8) -> Bool {
        switch transfer {
        case 4, 5, 6, 7, 8, 9, 10, 14, 15, 16, 17, 18, 19, 20, 21, 22:
            return true
        default:
            return false
        }
    }

    /// Printing-density and log headers become scene-linear. Other non-video
    /// values are full-range code values in 0…1.
    static func linearCode(_ code: Float, bitDepth: Int, transfer: UInt8) -> Float {
        switch transfer {
        case 1, 3, 13:
            return ToneMap.cineonToLinear(code: code, bitDepth: bitDepth)
        default:
            return ToneMap.normalizedCode(code, bitDepth: bitDepth)
        }
    }

    /// Code value → encoded display unit. Video stays normalized. Scene-linear
    /// and log use the filmic curve; the appex preview path applies the OCIO
    /// cube instead when a LUT is present.
    static func displayCode(_ code: Float, bitDepth: Int, transfer: UInt8) -> Float {
        if isVideoTransfer(transfer) {
            return ToneMap.normalizedCode(code, bitDepth: bitDepth)
        }
        return ToneMap.acesFilmic(linearCode(code, bitDepth: bitDepth, transfer: transfer))
    }

    private static func normalizedCode(_ code: Float, bitDepth: Int) -> Float {
        ToneMap.normalizedCode(code, bitDepth: bitDepth)
    }

    static func quantize(_ unit: Float) -> UInt8 {
        UInt8((min(max(unit, 0), 1) * 255).rounded())
    }

    private static func componentsPerPixel(_ descriptor: UInt8) -> Int? {
        switch descriptor {
        case 50: return 3
        case 51: return 4
        case 6: return 1
        default: return nil
        }
    }

    private static func bytesPerPixel(bitDepth: Int, components: Int, packing: UInt16) -> Int? {
        if bitDepth == 8 && packing == 0 {
            return components
        }
        if bitDepth == 16 && (packing == 0 || packing == 1) {
            return components * 2
        }
        if bitDepth == 10 && packing == 1 && components == 3 {
            return 4
        }
        return nil
    }

    private static func sample(
        row: Data,
        x: Int,
        bitDepth: Int,
        components: Int,
        packing: UInt16,
        littleEndian: Bool
    ) -> (Float, Float, Float) {
        if bitDepth == 10 && packing == 1 {
            let word = readU32(row, x * 4, littleEndian)
            return (
                Float((word >> 22) & 0x3FF),
                Float((word >> 12) & 0x3FF),
                Float((word >> 2) & 0x3FF)
            )
        }
        if bitDepth == 16 {
            let base = x * components * 2
            let r = Float(readU16(row, base, littleEndian))
            if components == 1 {
                return (r, r, r)
            }
            return (
                r,
                Float(readU16(row, base + 2, littleEndian)),
                Float(readU16(row, base + 4, littleEndian))
            )
        }
        let base = x * components
        let r = Float(row[base])
        if components == 1 {
            return (r, r, r)
        }
        return (r, Float(row[base + 1]), Float(row[base + 2]))
    }
}

func imageFromLinear(_ pixels: [Float], width: Int, height: Int) throws -> CGImage {
    let colorSpace = CGColorSpace(name: CGColorSpace.extendedLinearSRGB) ?? CGColorSpaceCreateDeviceRGB()
    let bitmapInfo = CGBitmapInfo(
        rawValue: CGBitmapInfo.floatComponents.rawValue
            | CGBitmapInfo.byteOrder32Little.rawValue
            | CGImageAlphaInfo.premultipliedLast.rawValue
    )
    let data = pixels.withUnsafeBufferPointer { Data(buffer: $0) }
    guard
        let provider = CGDataProvider(data: data as CFData),
        let image = CGImage(
            width: width,
            height: height,
            bitsPerComponent: 32,
            bitsPerPixel: 128,
            bytesPerRow: width * MemoryLayout<Float>.size * 4,
            space: colorSpace,
            bitmapInfo: bitmapInfo,
            provider: provider,
            decode: nil,
            shouldInterpolate: true,
            intent: .defaultIntent
        )
    else {
        throw DPXError.unsupported("Could not allocate a linear preview bitmap.")
    }
    return image
}

func imageFromRGBA(_ pixels: [UInt8], width: Int, height: Int) throws -> CGImage {
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
    let bitmapInfo = CGBitmapInfo(
        rawValue: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
    )
    guard
        let provider = CGDataProvider(data: Data(pixels) as CFData),
        let image = CGImage(
            width: width,
            height: height,
            bitsPerComponent: 8,
            bitsPerPixel: 32,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: bitmapInfo,
            provider: provider,
            decode: nil,
            shouldInterpolate: true,
            intent: .defaultIntent
        )
    else {
        throw DPXError.unsupported("Could not allocate a preview bitmap.")
    }
    return image
}

private func read(_ handle: FileHandle, offset: UInt64, count: Int) throws -> Data {
    try handle.seek(toOffset: offset)
    let data = try handle.read(upToCount: count) ?? Data()
    return data
}

private func readU16(_ data: Data, _ offset: Int, _ littleEndian: Bool) -> UInt16 {
    let lo = Int(data[offset])
    let hi = Int(data[offset + 1])
    if littleEndian {
        return UInt16(lo | (hi << 8))
    }
    return UInt16((lo << 8) | hi)
}

private func readU32(_ data: Data, _ offset: Int, _ littleEndian: Bool) -> UInt32 {
    let b0 = UInt32(data[offset])
    let b1 = UInt32(data[offset + 1])
    let b2 = UInt32(data[offset + 2])
    let b3 = UInt32(data[offset + 3])
    if littleEndian {
        return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)
    }
    return (b0 << 24) | (b1 << 16) | (b2 << 8) | b3
}
