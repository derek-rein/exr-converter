import Darwin
import Foundation

/// JSON written by the Python app. See ``src/core/quicklook_prefs.py``.
enum QuickLookPreferences {
    static let appGroupID = "83546T4BT8.com.vfxtools.exrconverter"
    static let jsonName = "quicklook.json"
    static let fallbackRelative = "Library/Application Support/EXR Converter/quicklook.json"

    static func isEnabled(_ key: String, fileManager: FileManager = .default) -> Bool {
        let map = load(fileManager: fileManager)
        return map[key] ?? FormatCatalog.default(for: key)
    }

    static func load(fileManager: FileManager = .default) -> [String: Bool] {
        if let data = readGroup(fileManager), let parsed = parse(data) {
            return parsed
        }
        if let data = readFallback(), let parsed = parse(data) {
            return parsed
        }
        return FormatCatalog.defaults
    }

    static func parse(_ data: Data) -> [String: Bool]? {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let root = object as? [String: Any],
            let raw = root["formats"] as? [String: Any]
        else {
            return nil
        }
        var flags = FormatCatalog.defaults
        for format in FormatCatalog.all {
            if let value = raw[format.key] as? Bool {
                flags[format.key] = value
            } else if let number = raw[format.key] as? NSNumber {
                flags[format.key] = number.boolValue
            }
        }
        return flags
    }

    private static func readGroup(_ fileManager: FileManager) -> Data? {
        guard
            let container = fileManager.containerURL(
                forSecurityApplicationGroupIdentifier: appGroupID)
        else {
            return nil
        }
        return try? Data(contentsOf: container.appendingPathComponent(jsonName))
    }

    /// Sandbox ``homeDirectoryForCurrentUser`` is the appex container, not the
    /// real home. The temporary-exception entitlement is relative to the real
    /// home, which ``getpwuid`` still returns.
    private static func readFallback() -> Data? {
        guard let pw = getpwuid(getuid()), let dir = pw.pointee.pw_dir else {
            return nil
        }
        let home = URL(fileURLWithPath: String(cString: dir), isDirectory: true)
        return try? Data(contentsOf: home.appendingPathComponent(fallbackRelative))
    }
}

/// Thrown when the user turned this format off. Quick Look may then use another
/// preview, or show no preview when this extension is the only provider.
struct QuickLookDeclined: Error, CustomNSError {
    static let errorDomain = "com.vfxtools.exrconverter.quicklook"
    var errorCode: Int { 1 }
    var errorUserInfo: [String: Any] {
        [
            NSLocalizedDescriptionKey: "Quick Look for this format is turned off in EXR Converter."
        ]
    }
}
