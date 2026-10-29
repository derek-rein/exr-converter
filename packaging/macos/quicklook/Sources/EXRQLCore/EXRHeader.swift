import Foundation

/// The few OpenEXR header attributes the preview uses to pick an input color
/// space. Reads the first part's header only; multipart files share the
/// layout for their first header.
struct EXRHeader {
    /// ``string`` attributes by name.
    var strings: [String: String] = [:]
    /// R, G, B, white xy, from the ``chromaticities`` attribute.
    var chromaticities: [Float]?
    /// Pixel size from ``dataWindow``.
    var dataSize: (width: Int, height: Int)?

    static func read(url: URL, limit: Int = 1 << 18) -> EXRHeader? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        let data = handle.readData(ofLength: limit)
        return parse(data)
    }

    static func parse(_ data: Data) -> EXRHeader? {
        let bytes = [UInt8](data)
        guard
            bytes.count >= 8,
            bytes[0] == 0x76, bytes[1] == 0x2F, bytes[2] == 0x31, bytes[3] == 0x01
        else {
            return nil
        }
        var header = EXRHeader()
        var pos = 8
        func cString() -> String? {
            guard pos < bytes.count, let end = bytes[pos...].firstIndex(of: 0) else { return nil }
            let text = String(decoding: bytes[pos..<end], as: UTF8.self)
            pos = end + 1
            return text
        }
        while pos < bytes.count {
            guard let name = cString(), !name.isEmpty else { break }
            guard let type = cString(), pos + 4 <= bytes.count else { break }
            let size = Int(Int32(bitPattern: le32(bytes, pos)))
            pos += 4
            guard size >= 0, pos + size <= bytes.count else { break }
            let value = Array(bytes[pos..<(pos + size)])
            pos += size
            switch type {
            case "string":
                header.strings[name] = String(decoding: value, as: UTF8.self)
            case "chromaticities" where size == 32:
                header.chromaticities = (0..<8).map { Float(bitPattern: le32(value, $0 * 4)) }
            case "box2i" where name == "dataWindow" && size == 16:
                let xMin = Int(Int32(bitPattern: le32(value, 0)))
                let yMin = Int(Int32(bitPattern: le32(value, 4)))
                let xMax = Int(Int32(bitPattern: le32(value, 8)))
                let yMax = Int(Int32(bitPattern: le32(value, 12)))
                if xMax >= xMin, yMax >= yMin {
                    header.dataSize = (xMax - xMin + 1, yMax - yMin + 1)
                }
            default:
                break
            }
        }
        return header
    }
}

private func le32(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
    UInt32(bytes[offset])
        | (UInt32(bytes[offset + 1]) << 8)
        | (UInt32(bytes[offset + 2]) << 16)
        | (UInt32(bytes[offset + 3]) << 24)
}
