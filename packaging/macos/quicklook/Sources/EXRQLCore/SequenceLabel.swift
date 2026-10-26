import Foundation

/// Filename-only sequence identity. Sibling frames are readable when the
/// temporary-exception entitlement covers the directory. A failed listing is
/// just the selected frame.
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

    func filename(forFrame value: Int) -> String {
        "\(name)\(separator)\(padded(value)).\(ext)"
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

    /// Sorted frame numbers, or nil when the directory cannot be listed or is
    /// too large to trust. An empty match still returns the selected frame.
    static func listedFrames(directory: URL, frame: SequenceFrame) -> [Int]? {
        let names: [String]
        do {
            names = try FileManager.default.contentsOfDirectory(atPath: directory.path)
        } catch {
            return nil
        }
        if names.count > maxDirectoryEntries {
            return nil
        }
        var found = Set<Int>()
        for name in names {
            if name.hasPrefix(".") {
                continue
            }
            guard let other = SequenceFrame.parse(filename: name), other.matchesSequence(frame) else {
                continue
            }
            found.insert(other.frame)
        }
        if found.isEmpty {
            return [frame.frame]
        }
        return found.sorted()
    }

    static func frameRange(directory: URL, frame: SequenceFrame) -> ClosedRange<Int>? {
        guard
            let frames = listedFrames(directory: directory, frame: frame),
            frames.count >= 2,
            let low = frames.first,
            let high = frames.last,
            low != high
        else {
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

/// Frames the flipbook can open. ``frames`` is the selected frame alone when
/// the directory listing fails.
struct SequencePlaylist {
    let directory: URL
    let identity: SequenceFrame
    let frames: [Int]

    var canPlay: Bool { frames.count >= 2 }

    func fileURL(at index: Int) -> URL {
        directory.appendingPathComponent(identity.filename(forFrame: frames[index]))
    }

    func caption(at index: Int) -> String {
        let frame = frames[index]
        guard canPlay, let first = frames.first, let last = frames.last else {
            return "\(identity.name)  ·  frame \(identity.padded(frame))"
        }
        return "\(identity.name)  ·  \(identity.padded(frame))  ·  \(identity.padded(first))–\(identity.padded(last))"
    }

    static func load(url: URL) -> SequencePlaylist? {
        guard let identity = SequenceFrame.parse(filename: url.lastPathComponent) else {
            return nil
        }
        let directory = url.deletingLastPathComponent()
        let frames = SequenceLabel.listedFrames(directory: directory, frame: identity) ?? [identity.frame]
        return SequencePlaylist(directory: directory, identity: identity, frames: frames)
    }

    func index(ofFrame frame: Int) -> Int {
        frames.firstIndex(of: frame) ?? 0
    }
}
