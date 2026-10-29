import CoreGraphics
import XCTest

@testable import EXRQLCore

final class CoreTests: XCTestCase {
    func testAcesFilmicEndpoints() {
        XCTAssertEqual(ToneMap.acesFilmic(0), 0, accuracy: 0.0001)
        XCTAssertEqual(ToneMap.acesFilmic(-1), 0, accuracy: 0.0001)
        let white = ToneMap.acesFilmic(1)
        XCTAssertGreaterThan(white, 0.75)
        XCTAssertLessThan(white, 0.9)
        XCTAssertEqual(ToneMap.acesFilmic(10_000), 1, accuracy: 0.02)
        let mid = ToneMap.acesFilmic(0.18)
        XCTAssertGreaterThan(mid, 0.15)
        XCTAssertLessThan(mid, 0.45)
    }

    func testCineonReferenceWhite() {
        let linear = ToneMap.cineonToLinear(code: 685, bitDepth: 10)
        XCTAssertEqual(linear, 1, accuracy: 0.001)
        let black = ToneMap.cineonToLinear(code: 95, bitDepth: 10)
        XCTAssertGreaterThan(black, 0)
        XCTAssertLessThan(black, 0.05)
        let from16 = ToneMap.cineonToLinear(code: 43890, bitDepth: 16)  // 685/1023 * 65535
        XCTAssertEqual(from16, 1, accuracy: 0.02)
    }

    func testSequenceParseDotAndUnderscore() {
        let dot = SequenceFrame.parse(filename: "shot.1001.exr")
        XCTAssertEqual(dot?.name, "shot")
        XCTAssertEqual(dot?.separator, ".")
        XCTAssertEqual(dot?.frame, 1001)
        XCTAssertEqual(dot?.padding, 4)
        XCTAssertEqual(dot?.ext, "exr")

        let under = SequenceFrame.parse(filename: "chs_010_099.dpx")
        XCTAssertEqual(under?.name, "chs_010")
        XCTAssertEqual(under?.separator, "_")
        XCTAssertEqual(under?.frame, 99)
        XCTAssertEqual(under?.paddedFrame, "099")

        XCTAssertNil(SequenceFrame.parse(filename: "beauty.exr"))
        XCTAssertNil(SequenceFrame.parse(filename: ".1001.exr"))
        XCTAssertNil(SequenceFrame.parse(filename: "shot.1001"))
    }

    func testSequenceRangeFromFilenames() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        for frame in [1001, 1002, 1010] {
            FileManager.default.createFile(
                atPath: dir.appendingPathComponent("plate.\(frame).exr").path,
                contents: Data()
            )
        }
        FileManager.default.createFile(
            atPath: dir.appendingPathComponent("other.1001.exr").path, contents: Data())
        let label = SequenceLabel.overlay(
            filename: "plate.1002.exr", directory: dir)
        XCTAssertEqual(label, "plate  ·  1001–1010")
        let alone = SequenceLabel.overlay(filename: "beauty.exr", directory: dir)
        XCTAssertNil(alone)
    }

    func testPreferencesJsonHonorsExplicitFalseAndFillsDefaults() throws {
        let json = """
        {"version": 1, "formats": {"exr": false, "png": true}}
        """.data(using: .utf8)!
        let parsed = try XCTUnwrap(QuickLookPreferences.parse(json))
        XCTAssertEqual(parsed["exr"], false)
        XCTAssertEqual(parsed["png"], true)
        XCTAssertEqual(parsed["dpx"], true)
        XCTAssertEqual(parsed["jpeg"], false)
        XCTAssertNil(QuickLookPreferences.parse(Data("nope".utf8)))
    }

    func testCatalogDefaultsMatchProductChoices() {
        XCTAssertEqual(FormatCatalog.default(for: "exr"), true)
        XCTAssertEqual(FormatCatalog.default(for: "dpx"), true)
        XCTAssertEqual(FormatCatalog.default(for: "tiff"), false)
        XCTAssertEqual(FormatCatalog.default(for: "dng"), false)
        XCTAssertEqual(FormatCatalog.format(forExtension: ".JPG")?.key, "jpeg")
    }

    func testEightBitLinearDpxToneMapsFullScale() throws {
        let url = try writeDPX(
            width: 1,
            height: 1,
            bitDepth: 8,
            packing: 0,
            transfer: 2,
            pixelBytes: [255, 255, 255]
        )
        let image = try DPXDecoder.decode(url: url, maxPixel: 64)
        let px = try readSRGB(image)
        XCTAssertTrue((190...220).contains(Int(px[0])))
        XCTAssertEqual(px[0], px[1])
        XCTAssertEqual(px[1], px[2])
    }

    func testTenBitLogDpxReferenceWhite() throws {
        let code: UInt32 = 685
        let word = (code << 22) | (code << 12) | (code << 2)
        let url = try writeDPX(
            width: 1,
            height: 1,
            bitDepth: 10,
            packing: 1,
            transfer: 3,
            pixelBytes: [
                UInt8((word >> 24) & 0xFF),
                UInt8((word >> 16) & 0xFF),
                UInt8((word >> 8) & 0xFF),
                UInt8(word & 0xFF),
            ]
        )
        let image = try DPXDecoder.decode(url: url, maxPixel: 64)
        let px = try readSRGB(image)
        XCTAssertTrue((190...220).contains(Int(px[0])))
        XCTAssertEqual(px[0], px[1])
        XCTAssertEqual(px[1], px[2])
    }

    func testCompressedDpxIsRejected() throws {
        let url = try writeDPX(
            width: 1,
            height: 1,
            bitDepth: 8,
            packing: 0,
            transfer: 2,
            encoding: 1,
            pixelBytes: [0, 0, 0]
        )
        XCTAssertThrowsError(try DPXDecoder.decode(url: url, maxPixel: 32))
    }

    func testShaperClampsNonPositiveAndSpansTheLattice() {
        XCTAssertEqual(ViewLUT.shaper(0, minExp: -10, maxExp: 6), 0)
        XCTAssertEqual(ViewLUT.shaper(-4, minExp: -10, maxExp: 6), 0)
        XCTAssertEqual(ViewLUT.shaper(exp2(-10), minExp: -10, maxExp: 6), 0, accuracy: 1e-5)
        XCTAssertEqual(ViewLUT.shaper(exp2(6), minExp: -10, maxExp: 6), 1, accuracy: 1e-4)
        let mid = ViewLUT.shaper(0.18, minExp: -10, maxExp: 6)
        XCTAssertGreaterThan(mid, 0.4)
        XCTAssertLessThan(mid, 0.55)
    }

    func testViewLUTParseAndSample() throws {
        let lut = try XCTUnwrap(ViewLUT.parse(solidRedLUT()))
        XCTAssertEqual(lut.cubeSize, 2)
        XCTAssertEqual(lut.sourceName, "ACEScg")
        XCTAssertEqual(lut.displayName, "sRGB")
        XCTAssertEqual(lut.viewName, "View")
        let rgb = lut.sample(r: 0.2, g: 0.2, b: 0.2)
        XCTAssertEqual(rgb.0, 1, accuracy: 0.001)
        XCTAssertEqual(rgb.1, 0, accuracy: 0.001)
        XCTAssertEqual(rgb.2, 0, accuracy: 0.001)
        XCTAssertNil(ViewLUT.parse(Data("nope".utf8)))
    }

    func testColorCubeUsesTheLattice() throws {
        let lut = try XCTUnwrap(ViewLUT.parse(solidRedLUT()))
        let space = try XCTUnwrap(CGColorSpace(name: CGColorSpace.extendedLinearSRGB))
        let info = CGBitmapInfo.floatComponents.rawValue
            | CGBitmapInfo.byteOrder32Little.rawValue
            | CGImageAlphaInfo.premultipliedLast.rawValue
        let context = try XCTUnwrap(
            CGContext(
                data: nil,
                width: 1,
                height: 1,
                bitsPerComponent: 32,
                bytesPerRow: 16,
                space: space,
                bitmapInfo: info
            ))
        let gray = try XCTUnwrap(CGColor(colorSpace: space, components: [0.2, 0.2, 0.2, 1]))
        context.setFillColor(gray)
        context.fill(CGRect(x: 0, y: 0, width: 1, height: 1))
        let source = try XCTUnwrap(context.makeImage())
        let mapped = try XCTUnwrap(ColorCube.apply(image: source, lut: lut))
        let px = try readSRGB(mapped)
        XCTAssertGreaterThan(Int(px[0]), 200)
        XCTAssertLessThan(Int(px[1]), 30)
        XCTAssertLessThan(Int(px[2]), 30)
    }

    func testPlaylistPlaysListedFramesAndFallsBackToOne() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        for frame in [1001, 1003, 1002] {
            FileManager.default.createFile(
                atPath: dir.appendingPathComponent("plate.\(frame).exr").path,
                contents: Data()
            )
        }
        let url = dir.appendingPathComponent("plate.1002.exr")
        let playlist = try XCTUnwrap(SequencePlaylist.load(url: url))
        XCTAssertEqual(playlist.frames, [1001, 1002, 1003])
        XCTAssertTrue(playlist.canPlay)
        XCTAssertEqual(playlist.index(ofFrame: 1002), 1)
        XCTAssertEqual(playlist.fileURL(at: 0).lastPathComponent, "plate.1001.exr")
        XCTAssertEqual(playlist.caption(at: 1), "plate  ·  1002  ·  1001–1003")

        let missing = dir.appendingPathComponent("gone")
        try FileManager.default.createDirectory(at: missing, withIntermediateDirectories: true)
        let alone = missing.appendingPathComponent("plate.1005.exr")
        FileManager.default.createFile(atPath: alone.path, contents: Data())
        try FileManager.default.removeItem(at: missing)
        let fallback = try XCTUnwrap(SequencePlaylist.load(url: alone))
        XCTAssertEqual(fallback.frames, [1005])
        XCTAssertFalse(fallback.canPlay)
    }

    func testToneMapAssignsRedToRed() throws {
        let space = try XCTUnwrap(CGColorSpace(name: CGColorSpace.extendedLinearSRGB))
        let info = CGBitmapInfo.floatComponents.rawValue
            | CGBitmapInfo.byteOrder32Little.rawValue
            | CGImageAlphaInfo.premultipliedLast.rawValue
        let context = try XCTUnwrap(
            CGContext(
                data: nil,
                width: 1,
                height: 1,
                bitsPerComponent: 32,
                bytesPerRow: 16,
                space: space,
                bitmapInfo: info
            ))
        let red = try XCTUnwrap(CGColor(colorSpace: space, components: [0.18, 0, 0, 1]))
        context.setFillColor(red)
        context.fill(CGRect(x: 0, y: 0, width: 1, height: 1))
        let source = try XCTUnwrap(context.makeImage())
        let mapped = try XCTUnwrap(PreviewRenderer.toneMap(source))
        let px = try readSRGB(mapped)
        XCTAssertGreaterThan(Int(px[0]), 30)
        XCTAssertLessThan(Int(px[1]), 8)
        XCTAssertLessThan(Int(px[2]), 8)
    }

    private func solidRedLUT() -> Data {
        var data = Data()
        data.append(contentsOf: Array("EXQL".utf8))
        func u16(_ value: UInt16) {
            data.append(UInt8(value & 0xFF))
            data.append(UInt8(value >> 8))
        }
        func f32(_ value: Float) {
            var bits = value.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { data.append(contentsOf: $0) }
        }
        u16(1)
        u16(2)
        u16(1)
        u16(0)
        f32(-10)
        f32(6)
        let source = Data("ACEScg".utf8)
        let display = Data("sRGB".utf8)
        let view = Data("View".utf8)
        u16(UInt16(source.count))
        u16(UInt16(display.count))
        u16(UInt16(view.count))
        u16(0)
        data.append(source)
        data.append(display)
        data.append(view)
        for _ in 0..<8 {
            f32(1)
            f32(0)
            f32(0)
            f32(1)
        }
        return data
    }

    private func readSRGB(_ image: CGImage) throws -> [UInt8] {
        var out = [UInt8](repeating: 0, count: 4)
        let space = try XCTUnwrap(CGColorSpace(name: CGColorSpace.sRGB))
        let info = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
        let context = try XCTUnwrap(
            CGContext(
                data: &out,
                width: 1,
                height: 1,
                bitsPerComponent: 8,
                bytesPerRow: 4,
                space: space,
                bitmapInfo: info
            ))
        context.draw(image, in: CGRect(x: 0, y: 0, width: 1, height: 1))
        return out
    }

    private func writeDPX(
        width: UInt32,
        height: UInt32,
        bitDepth: UInt8,
        packing: UInt16,
        transfer: UInt8,
        encoding: UInt16 = 0,
        pixelBytes: [UInt8]
    ) throws -> URL {
        var header = Data(count: 832)
        header[0] = UInt8(ascii: "S")
        header[1] = UInt8(ascii: "D")
        header[2] = UInt8(ascii: "P")
        header[3] = UInt8(ascii: "X")
        put16(&header, 768, 0)
        put16(&header, 770, 1)
        put32(&header, 772, width)
        put32(&header, 776, height)
        header[800] = 50
        header[801] = transfer
        header[803] = bitDepth
        put16(&header, 804, packing)
        put16(&header, 806, encoding)
        put32(&header, 808, 832)
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension("dpx")
        var file = header
        file.append(contentsOf: pixelBytes)
        try file.write(to: url)
        return url
    }

    private func put16(_ data: inout Data, _ offset: Int, _ value: UInt16) {
        data[offset] = UInt8((value >> 8) & 0xFF)
        data[offset + 1] = UInt8(value & 0xFF)
    }

    private func put32(_ data: inout Data, _ offset: Int, _ value: UInt32) {
        data[offset] = UInt8((value >> 24) & 0xFF)
        data[offset + 1] = UInt8((value >> 16) & 0xFF)
        data[offset + 2] = UInt8((value >> 8) & 0xFF)
        data[offset + 3] = UInt8(value & 0xFF)
    }
}
