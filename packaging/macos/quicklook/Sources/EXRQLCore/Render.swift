import CoreGraphics
import CoreImage
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum PreviewOutcome {
    case decline
    case image(CGImage, label: String?)
}

enum PreviewError: Error, CustomStringConvertible {
    case unreadable(String)

    var description: String {
        switch self {
        case .unreadable(let reason):
            return reason
        }
    }
}

enum PreviewRenderer {
    static func decide(url: URL, maxPixel: Int) -> PreviewOutcome {
        switch frame(url: url, maxPixel: maxPixel) {
        case .decline:
            return .decline
        case .image(let image, _):
            let label = SequenceLabel.overlay(
                filename: url.lastPathComponent,
                directory: url.deletingLastPathComponent()
            )
            return .image(image, label: label)
        }
    }

    /// Picture only. The flipbook draws its own caption so a directory listing
    /// is not repeated for every frame.
    static func frame(url: URL, maxPixel: Int) -> PreviewOutcome {
        let ext = url.pathExtension
        guard let format = FormatCatalog.format(forExtension: ext) else {
            return .decline
        }
        if !QuickLookPreferences.isEnabled(format.key) {
            return .decline
        }
        do {
            let image = try render(url: url, format: format, maxPixel: max(maxPixel, 1))
            return .image(image, label: nil)
        } catch {
            let message = String(describing: error)
            if let fallback = errorImage(message: message) {
                return .image(fallback, label: nil)
            }
            return .decline
        }
    }

    static func pngData(image: CGImage, label: String?) throws -> (data: Data, size: CGSize) {
        let composited = try composite(image: image, label: label)
        let data = NSMutableData()
        guard
            let dest = CGImageDestinationCreateWithData(
                data, UTType.png.identifier as CFString, 1, nil)
        else {
            throw PreviewError.unreadable("Could not encode a PNG preview.")
        }
        CGImageDestinationAddImage(dest, composited, nil)
        guard CGImageDestinationFinalize(dest) else {
            throw PreviewError.unreadable("Could not encode a PNG preview.")
        }
        return (data as Data, CGSize(width: composited.width, height: composited.height))
    }

    static func drawThumbnail(
        image: CGImage, label: String?, in context: CGContext, size: CGSize
    ) {
        let rect = CGRect(origin: .zero, size: size)
        context.setFillColor(CGColor(gray: 0.08, alpha: 1))
        context.fill(rect)
        let fitted = aspectFit(imageSize: CGSize(width: image.width, height: image.height), in: rect)
        context.interpolationQuality = .high
        context.draw(image, in: fitted)
        if let label, size.height >= 96 {
            drawLabel(label, in: context, bounds: rect)
        }
    }

    private static func render(url: URL, format: QuickLookFormat, maxPixel: Int) throws -> CGImage {
        switch format.key {
        case "dpx":
            return try DPXDecoder.decode(url: url, maxPixel: maxPixel)
        case "dng":
            return try dngImage(url: url, maxPixel: maxPixel)
        default:
            return try imageIOImage(url: url, format: format, maxPixel: maxPixel)
        }
    }

    private static func imageIOImage(url: URL, format: QuickLookFormat, maxPixel: Int) throws -> CGImage {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else {
            throw PreviewError.unreadable("ImageIO could not open \(url.lastPathComponent).")
        }
        let thumbOptions: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixel,
            kCGImageSourceShouldAllowFloat: true,
            kCGImageSourceShouldCache: false,
        ]
        guard
            let image = CGImageSourceCreateThumbnailAtIndex(source, 0, thumbOptions as CFDictionary)
                ?? CGImageSourceCreateImageAtIndex(source, 0, thumbOptions as CFDictionary)
        else {
            throw PreviewError.unreadable("ImageIO could not decode \(url.lastPathComponent).")
        }
        guard format.toneMapLinear, shouldToneMap(image) else {
            return image
        }
        return toneMap(image) ?? image
    }

    /// ImageIO sometimes hands back an 8-bit sRGB EXR that it already mapped.
    /// Tone-mapping that again crushes the picture, so only wide buffers are fit.
    private static func shouldToneMap(_ image: CGImage) -> Bool {
        if image.bitsPerComponent > 8 || image.bitsPerPixel > 32 {
            return true
        }
        let name = (image.colorSpace?.name as String?) ?? ""
        return name.localizedCaseInsensitiveContains("linear")
            || name.localizedCaseInsensitiveContains("extended")
    }

    /// Scene-linear pixels through the baked OCIO cube, or the filmic curve when
    /// that cube is missing. The result is 8-bit sRGB.
    static func toneMap(_ image: CGImage) -> CGImage? {
        guard let linear = LinearRGBA.read(image) else { return nil }
        if let lut = ViewLUTStore.preferred() {
            if let cubed = ColorCube.apply(linear: linear, lut: lut) {
                return cubed
            }
            if let cpu = applyCPU(linear, lut: lut) {
                return cpu
            }
        }
        return filmic(linear)
    }

    private static func applyCPU(_ linear: LinearRGBA, lut: ViewLUT) -> CGImage? {
        let count = linear.width * linear.height
        var encoded = [UInt8](repeating: 0, count: count * 4)
        for i in 0..<count {
            let base = i * 4
            let rgb = lut.sample(
                r: linear.channels[base],
                g: linear.channels[base + 1],
                b: linear.channels[base + 2]
            )
            encoded[base] = DPXDecoder.quantize(rgb.0)
            encoded[base + 1] = DPXDecoder.quantize(rgb.1)
            encoded[base + 2] = DPXDecoder.quantize(rgb.2)
            encoded[base + 3] = DPXDecoder.quantize(linear.channels[base + 3])
        }
        return try? imageFromRGBA(encoded, width: linear.width, height: linear.height)
    }

    private static func filmic(_ linear: LinearRGBA) -> CGImage? {
        let count = linear.width * linear.height
        var encoded = [UInt8](repeating: 0, count: count * 4)
        for i in 0..<count {
            let base = i * 4
            encoded[base] = DPXDecoder.quantize(ToneMap.acesFilmic(linear.channels[base]))
            encoded[base + 1] = DPXDecoder.quantize(ToneMap.acesFilmic(linear.channels[base + 1]))
            encoded[base + 2] = DPXDecoder.quantize(ToneMap.acesFilmic(linear.channels[base + 2]))
            encoded[base + 3] = DPXDecoder.quantize(linear.channels[base + 3])
        }
        return try? imageFromRGBA(encoded, width: linear.width, height: linear.height)
    }

    private static func dngImage(url: URL, maxPixel: Int) throws -> CGImage {
        if let raw = CIRAWFilter(imageURL: url) {
            if let extent = raw.outputImage?.extent, extent.width > 0, extent.height > 0 {
                let long = max(extent.width, extent.height)
                if long > CGFloat(maxPixel) {
                    raw.scaleFactor = Float(maxPixel) / Float(long)
                }
            }
            if let output = raw.outputImage {
                let srgb = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
                let ci = CIContext(options: [.workingColorSpace: srgb])
                if let image = ci.createCGImage(output, from: output.extent) {
                    return image
                }
            }
        }
        if let source = CGImageSourceCreateWithURL(url as CFURL, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        {
            return image
        }
        throw PreviewError.unreadable(
            "The system RAW decoder could not open this DNG. CinemaDNG look is best-effort.")
    }

    static func composite(image: CGImage, label: String?) throws -> CGImage {
        guard let label, !label.isEmpty else { return image }
        let width = image.width
        let height = image.height
        let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
        guard
            let context = CGContext(
                data: nil,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: colorSpace,
                bitmapInfo: bitmapInfo
            )
        else {
            return image
        }
        let bounds = CGRect(x: 0, y: 0, width: width, height: height)
        context.draw(image, in: bounds)
        drawLabel(label, in: context, bounds: bounds)
        return context.makeImage() ?? image
    }

    static func errorImage(message: String) -> CGImage? {
        let width = 960
        let height = 540
        let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        guard
            let context = CGContext(
                data: nil,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: colorSpace,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
            )
        else {
            return nil
        }
        context.setFillColor(CGColor(gray: 0.12, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        drawLabel(message, in: context, bounds: CGRect(x: 0, y: 0, width: width, height: height), fillBar: false)
        return context.makeImage()
    }

    static func drawLabel(
        _ label: String,
        in context: CGContext,
        bounds: CGRect,
        fillBar: Bool = true
    ) {
        let barHeight = min(max(bounds.height * 0.08, 36), 96)
        let bar = CGRect(x: 0, y: 0, width: bounds.width, height: fillBar ? barHeight : bounds.height)
        if fillBar {
            context.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 0.72))
            context.fill(bar)
        }
        let fontSize = min(max(bar.height * 0.38, 14), 32)
        let font = CTFontCreateWithName("Helvetica" as CFString, fontSize, nil)
        let color = CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1)
        let attrs: [CFString: Any] = [
            kCTFontAttributeName: font,
            kCTForegroundColorAttributeName: color,
        ]
        let attributed = CFAttributedStringCreate(nil, label as CFString, attrs as CFDictionary)
        guard let attributed else { return }
        let full = CTLineCreateWithAttributedString(attributed)
        let maxWidth = max(bounds.width - 32, 1)
        let line = CTLineCreateTruncatedLine(full, Double(maxWidth), .end, nil) ?? full
        let ascent = CTFontGetAscent(font)
        let descent = CTFontGetDescent(font)
        let textY = fillBar
            ? (bar.height - (ascent + descent)) / 2 + descent
            : (bounds.height - (ascent + descent)) / 2 + descent
        context.textPosition = CGPoint(x: 16, y: textY)
        CTLineDraw(line, context)
    }

    static func aspectFit(imageSize: CGSize, in bounds: CGRect) -> CGRect {
        guard imageSize.width > 0, imageSize.height > 0 else { return bounds }
        let scale = min(bounds.width / imageSize.width, bounds.height / imageSize.height)
        let width = imageSize.width * scale
        let height = imageSize.height * scale
        return CGRect(
            x: bounds.midX - width / 2,
            y: bounds.midY - height / 2,
            width: width,
            height: height
        )
    }
}
