import Cocoa
import Foundation
import QuickLookUI

/// Referenced from Info.plist only. Keep the class live for the linker.
@used
let exrqlKeepPreviewProvider: AnyClass = PreviewProvider.self

/// View-based preview (``QLIsDataBasedPreview`` is false). The view plays a
/// numbered sequence when sibling frames can be read, and otherwise shows the
/// selected file.
public final class PreviewProvider: NSViewController, QLPreviewingController {
    private let flipbook = FlipbookView(frame: NSRect(x: 0, y: 0, width: 960, height: 540))
    private var scopedURL: URL?
    private var didScope = false

    override public func loadView() {
        flipbook.autoresizingMask = [.width, .height]
        view = flipbook
    }

    override public var nibName: NSNib.Name? { nil }

    public func preparePreviewOfFile(
        at url: URL,
        completionHandler handler: @escaping @Sendable (Error?) -> Void
    ) {
        releaseScope()
        scopedURL = url
        didScope = url.startAccessingSecurityScopedResource()
        guard let format = FormatCatalog.format(forExtension: url.pathExtension) else {
            handler(QuickLookDeclined())
            return
        }
        if !QuickLookPreferences.isEnabled(format.key) {
            handler(QuickLookDeclined())
            return
        }
        flipbook.open(url: url)
        handler(nil)
    }

    deinit {
        flipbook.stop()
        releaseScope()
    }

    private func releaseScope() {
        if didScope, let scopedURL {
            scopedURL.stopAccessingSecurityScopedResource()
        }
        didScope = false
        scopedURL = nil
    }
}
