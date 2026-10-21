import Foundation

/// Filename-only sequence identity. The Quick Look sandbox can read the file
/// it was handed and usually cannot list sibling frames, so the frame range is
/// optional.
struct SequenceFrame: Equatable {
    let name: String
    let separator: Character
    let frame: Int
    let padding: Int
    let ext: String

    var paddedFrame: String { padded(frame) }

    func padded(_ value: Int) -> String {
        String(format: "%0\(padding)d", value)
    }

    func matchesSequence(_ other: SequenceFrame) -> Bool {
        name == other.name && separator == other.separator && ext.caseInsensitiveCompare(other.ext) == .orderedSame
    }

    /// ``shot.1001.exr`` and ``shot_1001.exr``. A bare ``beauty.exr`` is not a frame.
    static func parse(filename: String) -> SequenceFrame? {
        guard let dot = filename.lastIndex(of: ".") else { return nil }
        let extStart = filename.index(after: dot)
        let ext = String(filename[extStart...])
        if ext.isEmpty || !ext.allSatisfy({ $0.isLetter || $0.isNumber }) {
            return nil
        }
        let stem = filename[..<dot]
        guard let sepIndex = stem.lastIndex(where: { $0 == "." || $0 == "_" }) else { return nil }
        let frameStart = stem.index(after: sepIndex)
        let frameStr = String(stem[frameStart...])
        if frameStr.isEmpty || !frameStr.allSatisfy(\.isNumber) {
            return nil
        }
        guard let frame = Int(frameStr) else { return nil }
        let name = String(stem[..<sepIndex])
        if name.isEmpty {
            return nil
        }
        return SequenceFrame(
            name: name,
            separator: stem[sepIndex],
            frame: frame,
            padding: frameStr.count,
            ext: ext
        )
    }
}

enum SequenceLabel {
    /// Cap so a huge directory cannot stall a preview. Past this, the range is
    /// omitted rather than shown from a partial listing.
    static let maxDirectoryEntries = 50_000

    static func frameRange(directory: URL, frame: SequenceFrame) -> ClosedRange<Int>? {
        let names: [String]
        do {
            names = try FileManager.default.contentsOfDirectory(atPath: directory.path)
        } catch {
            return nil
        }
        if names.count > maxDirectoryEntries {
            return nil
        }
        var low = frame.frame
        var high = frame.frame
        var found = 0
        for name in names {
            if name.hasPrefix(".") {
                continue
            }
            guard let other = SequenceFrame.parse(filename: name), other.matchesSequence(frame) else {
                continue
            }
            found += 1
            low = min(low, other.frame)
            high = max(high, other.frame)
        }
        if found < 2 || low == high {
            return nil
        }
        return low...high
    }

    /// ``shot  ·  1001–1100`` when the folder listing works, otherwise the frame number.
    static func overlay(filename: String, directory: URL?) -> String? {
        guard let frame = SequenceFrame.parse(filename: filename) else { return nil }
        if let directory, let range = frameRange(directory: directory, frame: frame) {
            return "\(frame.name)  ·  \(frame.padded(range.lowerBound))–\(frame.padded(range.upperBound))"
        }
        return "\(frame.name)  ·  frame \(frame.paddedFrame)"
    }
}
