import Cocoa

/// Frame ruler under the picture, like the player's: frame numbers, a green
/// bar under the frames that are cached, and an orange playhead. Click or drag
/// to scrub.
final class TimelineView: NSView {
    var frames: [Int] = [] { didSet { needsDisplay = true } }
    var playhead = 0 { didSet { if playhead != oldValue { needsDisplay = true } } }
    var cached = IndexSet() { didSet { if cached != oldValue { needsDisplay = true } } }
    var onScrub: ((Int) -> Void)?

    private let inset: CGFloat = 10
    private static let accent = NSColor(srgbRed: 0.93, green: 0.55, blue: 0.16, alpha: 1)
    private static let cachedColor = NSColor(srgbRed: 0.30, green: 0.62, blue: 0.30, alpha: 1)

    override var isFlipped: Bool { true }

    private var track: (left: CGFloat, width: CGFloat) {
        (bounds.minX + inset, max(bounds.width - inset * 2, 1))
    }

    private func x(for index: Int) -> CGFloat {
        let (left, width) = track
        guard frames.count > 1 else { return left + width / 2 }
        return left + width * CGFloat(index) / CGFloat(frames.count - 1)
    }

    private func index(at point: NSPoint) -> Int {
        let (left, width) = track
        guard frames.count > 1 else { return 0 }
        let t = (point.x - left) / width
        let raw = Int((t * CGFloat(frames.count - 1)).rounded())
        return min(max(raw, 0), frames.count - 1)
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor(white: 0.13, alpha: 1).setFill()
        bounds.fill()
        guard !frames.isEmpty else { return }
        let (left, width) = track
        let barY = bounds.height - 8

        // Ruler: label every `step` frames so labels stay about 56 px apart.
        let perFrame = width / CGFloat(max(frames.count - 1, 1))
        let steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000]
        let step = steps.first { CGFloat($0) * perFrame >= 56 } ?? steps.last!
        let minor = max(step / 5, 1)
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 9, weight: .regular),
            .foregroundColor: NSColor(white: 0.62, alpha: 1),
        ]
        let tick = NSBezierPath()
        for (i, frame) in frames.enumerated() where frame % minor == 0 {
            let px = x(for: i).rounded() + 0.5
            let major = frame % step == 0
            tick.move(to: NSPoint(x: px, y: major ? 13 : 17))
            tick.line(to: NSPoint(x: px, y: barY - 3))
            if major {
                let label = "\(frame)" as NSString
                let size = label.size(withAttributes: attrs)
                label.draw(at: NSPoint(x: px - size.width / 2, y: 1), withAttributes: attrs)
            }
        }
        NSColor(white: 0.45, alpha: 1).setStroke()
        tick.lineWidth = 1
        tick.stroke()

        // Cache bar.
        NSColor(white: 0.22, alpha: 1).setFill()
        NSRect(x: left, y: barY, width: width, height: 4).fill()
        Self.cachedColor.setFill()
        let half = max(perFrame / 2, 0.5)
        for range in cached.rangeView {
            guard let first = range.first, let last = range.last else { continue }
            let x0 = max(x(for: first) - half, left)
            let x1 = min(x(for: last) + half, left + width)
            NSRect(x: x0, y: barY, width: max(x1 - x0, 1), height: 4).fill()
        }

        // Playhead.
        let px = x(for: min(max(playhead, 0), frames.count - 1))
        Self.accent.setStroke()
        let line = NSBezierPath()
        line.move(to: NSPoint(x: px, y: 12))
        line.line(to: NSPoint(x: px, y: barY))
        line.lineWidth = 1.5
        line.stroke()
        Self.accent.setFill()
        NSBezierPath(ovalIn: NSRect(x: px - 5, y: barY - 3, width: 10, height: 10)).fill()
        _ = dirtyRect
    }

    override func mouseDown(with event: NSEvent) {
        scrub(event)
    }

    override func mouseDragged(with event: NSEvent) {
        scrub(event)
    }

    private func scrub(_ event: NSEvent) {
        guard !frames.isEmpty else { return }
        let point = convert(event.locationInWindow, from: nil)
        onScrub?(index(at: point))
    }
}
