import CoreGraphics
import CoreImage
import Foundation

/// Log2 shaper on the CPU, then ``CIColorCube`` in a color-management-free
/// context so the display-encoded lattice is not gamma-encoded a second time.
/// Returns nil when Core Image cannot build the image; the caller uses the
/// filmic fallback.
enum ColorCube {
    private static let context = CIContext(options: [
        .workingColorSpace: NSNull(),
        .outputColorSpace: NSNull(),
    ])

    static func apply(image: CGImage, lut: ViewLUT) -> CGImage? {
        guard let linear = LinearRGBA.read(image) else { return nil }
        return apply(linear: linear, lut: lut)
    }

    static func apply(linear: LinearRGBA, lut: ViewLUT) -> CGImage? {
        let count = linear.width * linear.height
        guard count > 0, lut.samples.count == lut.cubeSize * lut.cubeSize * lut.cubeSize * 4 else {
            return nil
        }
        var shaped = [Float](repeating: 0, count: count * 4)
        for i in 0..<count {
            let base = i * 4
            shaped[base] = ViewLUT.shaper(linear.channels[base], minExp: lut.minExp, maxExp: lut.maxExp)
            shaped[base + 1] = ViewLUT.shaper(
                linear.channels[base + 1], minExp: lut.minExp, maxExp: lut.maxExp)
            shaped[base + 2] = ViewLUT.shaper(
                linear.channels[base + 2], minExp: lut.minExp, maxExp: lut.maxExp)
            shaped[base + 3] = min(max(linear.channels[base + 3], 0), 1)
        }
        let bytes = shaped.withUnsafeBufferPointer { Data(buffer: $0) }
        let device = CGColorSpaceCreateDeviceRGB()
        let ci = CIImage(
            bitmapData: bytes,
            bytesPerRow: linear.width * MemoryLayout<Float>.size * 4,
            size: CGSize(width: linear.width, height: linear.height),
            format: .RGBAf,
            colorSpace: device
        )
        guard let filter = CIFilter(name: "CIColorCube") else { return nil }
        filter.setValue(ci, forKey: kCIInputImageKey)
        filter.setValue(lut.cubeSize, forKey: "inputCubeDimension")
        filter.setValue(lut.cubeData, forKey: "inputCubeData")
        guard let output = filter.outputImage, !output.extent.isInfinite, !output.extent.isNull else {
            return nil
        }
        let width = linear.width
        let height = linear.height
        var bitmap = [UInt8](repeating: 0, count: width * height * 4)
        bitmap.withUnsafeMutableBytes { raw in
            guard let base = raw.baseAddress else { return }
            context.render(
                output,
                toBitmap: base,
                rowBytes: width * 4,
                bounds: CGRect(x: 0, y: 0, width: width, height: height),
                format: .RGBA8,
                colorSpace: device
            )
        }
        return try? imageFromRGBA(bitmap, width: width, height: height)
    }
}

struct LinearRGBA {
    let width: Int
    let height: Int
    /// Straight RGBA, length ``width × height × 4``.
    let channels: [Float]

    static func read(_ image: CGImage) -> LinearRGBA? {
        let width = image.width
        let height = image.height
        guard width > 0, height > 0 else { return nil }
        guard
            let linear = CGColorSpace(name: CGColorSpace.extendedLinearSRGB),
            let context = CGContext(
                data: nil,
                width: width,
                height: height,
                bitsPerComponent: 32,
                bytesPerRow: width * MemoryLayout<Float>.size * 4,
                space: linear,
                bitmapInfo: CGBitmapInfo.floatComponents.rawValue
                    | CGBitmapInfo.byteOrder32Little.rawValue
                    | CGImageAlphaInfo.premultipliedLast.rawValue
            ),
            let raw = context.data
        else {
            return nil
        }
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        let count = width * height
        let floats = raw.bindMemory(to: Float.self, capacity: count * 4)
        var channels = [Float](repeating: 0, count: count * 4)
        for i in 0..<count {
            var r = floats[i * 4]
            var g = floats[i * 4 + 1]
            var b = floats[i * 4 + 2]
            let a = floats[i * 4 + 3]
            if a > 1e-4 {
                r /= a
                g /= a
                b /= a
            }
            channels[i * 4] = r
            channels[i * 4 + 1] = g
            channels[i * 4 + 2] = b
            channels[i * 4 + 3] = min(max(a, 0), 1)
        }
        return LinearRGBA(width: width, height: height, channels: channels)
    }
}
