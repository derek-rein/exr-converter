import CoreGraphics
import Foundation
import QuickLookUI

/// Referenced from Info.plist only. Keep the class live for the linker.
@used
let exrqlKeepPreviewProvider: AnyClass = PreviewProvider.self

/// Data-based preview (``QLIsDataBasedPreview``). On current macOS SDKs
/// ``QLPreviewProvider`` is a class in QuickLookUI, not a protocol in QuickLook.
public final class PreviewProvider: QLPreviewProvider, QLPreviewingController {
    public func providePreview(
        for request: QLFilePreviewRequest,
        completionHandler handler: @escaping @Sendable (QLPreviewReply?, Error?) -> Void
    ) {
        let url = request.fileURL
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing {
                url.stopAccessingSecurityScopedResource()
            }
        }
        switch PreviewRenderer.decide(url: url, maxPixel: 1920) {
        case .decline:
            handler(nil, QuickLookDeclined())
        case .image(let image, let label):
            do {
                let encoded = try PreviewRenderer.pngData(image: image, label: label)
                let reply = QLPreviewReply(
                    dataOfContentType: .png,
                    contentSize: encoded.size,
                    createDataUsing: { reply in
                        if let label, !label.isEmpty {
                            reply.title = label
                        }
                        return encoded.data
                    }
                )
                handler(reply, nil)
            } catch {
                handler(nil, error)
            }
        }
    }
}
