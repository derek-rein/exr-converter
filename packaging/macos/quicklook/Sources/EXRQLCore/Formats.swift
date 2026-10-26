import Foundation

/// Keep `key` and `defaultEnabled` aligned with `src/core/quicklook_formats.py`.
struct QuickLookFormat {
    let key: String
    let extensions: [String]
    let defaultEnabled: Bool
    /// Tone-map float / linear pixels (OpenEXR, and TIFF when the buffer is linear).
    let toneMapLinear: Bool
}

enum FormatCatalog {
    static let all: [QuickLookFormat] = [
        QuickLookFormat(key: "exr", extensions: ["exr"], defaultEnabled: true, toneMapLinear: true),
        QuickLookFormat(key: "dpx", extensions: ["dpx"], defaultEnabled: true, toneMapLinear: true),
        QuickLookFormat(
            key: "tiff", extensions: ["tif", "tiff"], defaultEnabled: false, toneMapLinear: true),
        QuickLookFormat(key: "png", extensions: ["png"], defaultEnabled: false, toneMapLinear: false),
        QuickLookFormat(
            key: "jpeg", extensions: ["jpg", "jpeg"], defaultEnabled: false, toneMapLinear: false),
        QuickLookFormat(key: "webp", extensions: ["webp"], defaultEnabled: false, toneMapLinear: false),
        QuickLookFormat(key: "dng", extensions: ["dng"], defaultEnabled: false, toneMapLinear: false),
    ]

    static var defaults: [String: Bool] {
        Dictionary(uniqueKeysWithValues: all.map { ($0.key, $0.defaultEnabled) })
    }

    static func format(forExtension ext: String) -> QuickLookFormat? {
        let cleaned = ext.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return all.first { $0.extensions.contains(cleaned) }
    }

    static func `default`(for key: String) -> Bool {
        all.first { $0.key == key }?.defaultEnabled ?? false
    }
}
