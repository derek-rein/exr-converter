import CoreGraphics
import XCTest

@testable import EXRQLCore

final class PlaybackTests: XCTestCase {
    func testEXRHeaderReadsColorAttributes() throws {
        var data = Data([0x76, 0x2F, 0x31, 0x01, 0x02, 0x00, 0x00, 0x00])
        func attr(_ name: String, _ type: String, _ value: Data) {
            data.append(Data(name.utf8))
            data.append(0)
            data.append(Data(type.utf8))
            data.append(0)
            appendLE32(&data, UInt32(value.count))
            data.append(value)
        }
        attr("exrconverter:dstColorSpace", "string", Data("ACES - ACES2065-1".utf8))
        var chroma = Data()
        for v: Float in [0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290] {
            appendLE32(&chroma, v.bitPattern)
        }
        attr("chromaticities", "chromaticities", chroma)
        var window = Data()
        for v: Int32 in [0, 0, 1919, 1079] {
            appendLE32(&window, UInt32(bitPattern: v))
        }
        attr("dataWindow", "box2i", window)
        data.append(0)

        let header = try XCTUnwrap(EXRHeader.parse(data))
        XCTAssertEqual(header.strings["exrconverter:dstColorSpace"], "ACES - ACES2065-1")
        XCTAssertEqual(header.dataSize?.width, 1920)
        XCTAssertEqual(header.dataSize?.height, 1080)
        let xy = try XCTUnwrap(header.chromaticities)
        XCTAssertEqual(InputDetector.name(forChromaticities: xy), "Linear Rec.709 (sRGB)")
        XCTAssertNil(EXRHeader.parse(Data("nope".utf8)))
    }

    func testInputDetectionFollowsThePlayerOrder() throws {
        let catalog = try XCTUnwrap(InputCatalog.parse(catalogJSON()))
        var header = EXRHeader()
        header.strings["oiio:ColorSpace"] = "lin_rec709"
        header.strings["exrconverter:dstColorSpace"] = "ACES - ACES2065-1"
        let ours = InputDetector.detect(header: header, catalog: catalog)
        XCTAssertEqual(ours.name, "ACES2065-1")
        XCTAssertEqual(ours.source, "exrconverter:dstColorSpace")

        header.strings["exrconverter:dstColorSpace"] = nil
        header.chromaticities = [0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290]
        let chroma = InputDetector.detect(header: header, catalog: catalog)
        XCTAssertEqual(chroma.name, "Linear Rec.709 (sRGB)")
        XCTAssertEqual(chroma.source, "chromaticities")

        let plain = InputDetector.detect(header: EXRHeader(), catalog: catalog)
        XCTAssertEqual(plain.name, "ACEScg")
        XCTAssertEqual(plain.source, "default")
        XCTAssertTrue(try XCTUnwrap(plain.space).isIdentity)
    }

    func testInputSpaceAppliesCurveThenMatrix() throws {
        let catalog = try XCTUnwrap(InputCatalog.parse(catalogJSON()))
        let doubled = try XCTUnwrap(catalog.lookup("lin_ap0"))
        var pixels: [Float] = [0.1, 0.2, 0.3, 0.5]
        doubled.apply(to: &pixels)
        XCTAssertEqual(pixels[0], 0.2, accuracy: 1e-6)
        XCTAssertEqual(pixels[2], 0.6, accuracy: 1e-6)
        XCTAssertEqual(pixels[3], 0.5, accuracy: 1e-6)

        // Curve spans -0.125…1.25 in two segments: code 0.5625 is the midpoint.
        let log = try XCTUnwrap(catalog.lookup("Fake Log"))
        XCTAssertEqual(log.linearize(0.5625), 1, accuracy: 1e-5)
        XCTAssertEqual(log.linearize(1.25), 4, accuracy: 1e-5)
    }

    func testFrameCacheDropsTheFrameJustPlayed() {
        var cache = FrameCache<Int>(budget: 3)
        for index in 5...7 {
            XCTAssertTrue(cache.insert(index, cost: 1, at: index, playhead: 5, count: 10))
        }
        // Frame 8 is the furthest ahead of playhead 5, so it is dropped again.
        XCTAssertFalse(cache.insert(8, cost: 1, at: 8, playhead: 5, count: 10))
        XCTAssertEqual(Set(cache.indices), [5, 6, 7])
        // Once the playhead moves on, frame 5 (just played) makes room.
        XCTAssertTrue(cache.insert(8, cost: 1, at: 8, playhead: 6, count: 10))
        XCTAssertEqual(Set(cache.indices), [6, 7, 8])
        XCTAssertNil(cache.nextToDecode(playhead: 6, count: 10, busy: [], estimate: 1))

        var roomy = FrameCache<Int>(budget: 100)
        roomy.insert(0, cost: 1, at: 0, playhead: 0, count: 4)
        XCTAssertEqual(roomy.nextToDecode(playhead: 0, count: 4, busy: [1], estimate: 1), 2)
        XCTAssertEqual(roomy.nextToDecode(playhead: 3, count: 4, busy: [], estimate: 1), 3)
    }

    func testSceneFrameGainIsAnExposureShift() throws {
        let lut = try XCTUnwrap(ViewLUT.parse(identityLUT()))
        let linear = LinearRGBA(width: 2, height: 1, channels: [0.18, 0.18, 0.18, 1, 0.36, 0.36, 0.36, 1])
        let frame = SceneFrame.encode(linear, minExp: -10, maxExp: 6)
        let lifted = frame.shaped(for: lut, gain: 2)
        let plain = frame.shaped(for: lut, gain: 1)
        XCTAssertEqual(lifted[0], plain[4], accuracy: 1e-4)
        XCTAssertEqual(plain[4], ViewLUT.shaper(0.36, minExp: -10, maxExp: 6), accuracy: 1e-4)
        XCTAssertEqual(plain[3], 1, accuracy: 1e-4)

        let black = SceneFrame.encode(LinearRGBA(width: 1, height: 1, channels: [0, -1, 0, 1]), minExp: -10, maxExp: 6)
        XCTAssertEqual(black.shaped(for: lut, gain: 50)[0], 0)
        XCTAssertEqual(black.shaped(for: lut, gain: 50)[1], 0)
    }

    func testViewBundlePicksDisplayP3OnP3Screens() throws {
        var data = Data("EXQB".utf8)
        appendLE16(&data, 1)
        appendLE16(&data, 2)
        appendLE16(&data, 0)
        appendLE16(&data, 1)
        for tag in ["displayP3", "sRGB"] {
            let blob = identityLUT()
            appendLE16(&data, UInt16(tag.utf8.count))
            data.append(Data(tag.utf8))
            appendLE32(&data, UInt32(blob.count))
            data.append(blob)
        }
        let bundle = try XCTUnwrap(ViewLUTBundle.parse(data))
        XCTAssertEqual(bundle.choices.count, 2)
        XCTAssertTrue(bundle.choices[0].isDisplayP3)
        XCTAssertFalse(bundle.choices[1].isDisplayP3)
        XCTAssertEqual(bundle.defaultIndex(preferP3: true), 0)
        XCTAssertEqual(bundle.defaultIndex(preferP3: false), 1)
        XCTAssertNil(ViewLUTBundle.parse(data.prefix(20)))
    }

    // MARK: Fixtures

    private func catalogJSON() -> Data {
        let json = """
            {"version":1,"target":"ACEScg","curve_min":-0.125,"curve_max":1.25,"spaces":[
             {"name":"ACES2065-1","aliases":["ACES - ACES2065-1","lin_ap0"],"interop":"lin_ap0_scene",
              "matrix":[2,0,0,0,2,0,0,0,2]},
             {"name":"ACEScg","aliases":["lin_ap1"],"interop":"lin_ap1_scene","matrix":[1,0,0,0,1,0,0,0,1]},
             {"name":"Linear Rec.709 (sRGB)","aliases":["lin_rec709"],"interop":"lin_rec709_scene",
              "matrix":[0.613,0.340,0.047,0.070,0.916,0.013,0.021,0.110,0.870]},
             {"name":"Fake Log","aliases":[],"interop":"","matrix":[1,0,0,0,1,0,0,0,1],"curve":[0,1,4]}
            ]}
            """
        return Data(json.utf8)
    }

    /// 2³ cube whose output is its own 0…1 coordinate.
    private func identityLUT() -> Data {
        var data = Data("EXQL".utf8)
        appendLE16(&data, 1)
        appendLE16(&data, 2)
        appendLE16(&data, 1)
        appendLE16(&data, 0)
        appendLE32(&data, Float(-10).bitPattern)
        appendLE32(&data, Float(6).bitPattern)
        let names = ["ACEScg", "Display", "View"].map { Data($0.utf8) }
        for name in names {
            appendLE16(&data, UInt16(name.count))
        }
        appendLE16(&data, 0)
        names.forEach { data.append($0) }
        for b in 0..<2 {
            for g in 0..<2 {
                for r in 0..<2 {
                    for v: Float in [Float(r), Float(g), Float(b), 1] {
                        appendLE32(&data, v.bitPattern)
                    }
                }
            }
        }
        return data
    }

    private func appendLE16(_ data: inout Data, _ value: UInt16) {
        data.append(UInt8(value & 0xFF))
        data.append(UInt8(value >> 8))
    }

    private func appendLE32(_ data: inout Data, _ value: UInt32) {
        for shift in stride(from: 0, to: 32, by: 8) {
            data.append(UInt8((value >> UInt32(shift)) & 0xFF))
        }
    }
}
