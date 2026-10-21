import CoreGraphics
import Foundation
import QuickLook
import UniformTypeIdentifiers

/// Referenced from Info.plist only. Keep the class live for the linker.
@_used
let exrqlKeepPreviewProvider: AnyClass = PreviewProvider.self

/// Data-based preview (``QLIsDataBasedPreview``). ``QLPreviewProvider`` is the
/// current entry point; the older view-based ``QLPreviewingController`` is not
/// used, so Quick Look does not look for a nib.
public final class PreviewProvider: NSObject, QLPreviewProvider {
    override public init() {
        super.init()
    }

    public func providePreview(for request: QLFilePreviewRequest) async throws -> QLPreviewReply {
        let url = request.fileURL
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing {
                url.stopAccessingSecurityScopedResource()
            }
        }
        switch PreviewRenderer.decide(url: url, maxPixel: 1920) {
        case .decline:
            throw QuickLookDeclined()
        case .image(let image, let label):
            let encoded = try PreviewRenderer.pngData(image: image, label: label)
            return QLPreviewReply(dataOfContentType: .png, contentSize: encoded.size) { reply in
                if let label, !label.isEmpty {
                    reply.title = label
                }
                return encoded.data
            }
        }
    }
}
