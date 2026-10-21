import CoreGraphics
import Foundation
import QuickLookThumbnailing

/// Referenced from Info.plist only. Keep the class live for the linker.
@_used
let exrqlKeepThumbnailProvider: AnyClass = ThumbnailProvider.self

public final class ThumbnailProvider: QLThumbnailProvider {
    override public init() {
        super.init()
    }

    override public func provideThumbnail(
        for request: QLFileThumbnailRequest,
        _ handler: @escaping (QLThumbnailReply?, Error?) -> Void
    ) {
        let url = request.fileURL
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing {
                url.stopAccessingSecurityScopedResource()
            }
        }
        let maxPixel = Int(max(request.maximumSize.width, request.maximumSize.height).rounded(.up))
        switch PreviewRenderer.decide(url: url, maxPixel: max(maxPixel, 1)) {
        case .decline:
            handler(nil, QuickLookDeclined())
        case .image(let image, let label):
            let size = request.maximumSize
            let drawn = image
            let caption = label
            let reply = QLThumbnailReply(contextSize: size, drawing: { context in
                PreviewRenderer.drawThumbnail(image: drawn, label: caption, in: context, size: size)
                return true
            })
            handler(reply, nil)
        }
    }
}
