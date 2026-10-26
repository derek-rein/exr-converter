import Cocoa
import Foundation

/// Custom preview surface. Numbered frames autoplay at 24 fps when the
/// directory listing succeeds. A click pauses. The slider and the left/right
/// keys scrub when the host delivers the events. One frame, or a folder the
/// sandbox cannot list, stays on the selected file.
final class FlipbookView: NSView {
    private let slider = NSSlider(value: 0, minValue: 0, maxValue: 0, target: nil, action: nil)
    private let queue = DispatchQueue(
        label: "com.vfxtools.exrconverter.quicklook.flipbook",
        qos: .userInitiated
    )
    private var playlist: SequencePlaylist?
    private var urls: [URL] = []
    private var index = 0
    private var image: CGImage?
    private var caption = ""
    private var playing = false
    private var generation = 0
    private var timer: DispatchSourceTimer?
    private var maxPixel = 1920
    private let frameDelay: TimeInterval = 1.0 / 24.0

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        slider.isContinuous = true
        slider.target = self
        slider.action = #selector(scrubbed(_:))
        slider.controlSize = .small
        addSubview(slider)
    }

    required init?(coder: NSCoder) {
        fatalError("FlipbookView is created in code")
    }

    func open(url: URL) {
        stop()
        generation += 1
        let playlist = SequencePlaylist.load(url: url)
        self.playlist = playlist
        if let playlist, playlist.canPlay {
            urls = playlist.frames.indices.map { playlist.fileURL(at: $0) }
            index = playlist.index(ofFrame: playlist.identity.frame)
            maxPixel = 960
            playing = true
        } else {
            urls = [url]
            index = 0
            maxPixel = 1920
            playing = false
        }
        slider.isHidden = urls.count < 2
        slider.minValue = 0
        slider.maxValue = Double(max(urls.count - 1, 0))
        slider.doubleValue = Double(index)
        caption = playlist?.caption(at: index) ?? ""
        image = decode(urls[index], maxPixel: maxPixel)
            ?? PreviewRenderer.errorImage(message: "Could not read this frame.")
        needsLayout = true
        needsDisplay = true
        if playing {
            scheduleNext()
        }
    }

    func stop() {
        playing = false
        timer?.setEventHandler {}
        timer?.cancel()
        timer = nil
    }

    override func layout() {
        super.layout()
        let height: CGFloat = urls.count > 1 ? 22 : 0
        slider.frame = NSRect(x: 12, y: 6, width: max(bounds.width - 24, 0), height: height)
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        context.setFillColor(CGColor(gray: 0.08, alpha: 1))
        context.fill(bounds)
        let bar: CGFloat = urls.count > 1 ? 34 : 0
        let picture = CGRect(x: 0, y: bar, width: bounds.width, height: max(bounds.height - bar, 1))
        if let image {
            let fitted = PreviewRenderer.aspectFit(
                imageSize: CGSize(width: image.width, height: image.height),
                in: picture
            )
            context.interpolationQuality = .high
            context.draw(image, in: fitted)
        }
        if !caption.isEmpty {
            PreviewRenderer.drawLabel(caption, in: context, bounds: picture)
        }
        _ = dirtyRect
    }

    override var acceptsFirstResponder: Bool { true }

    override func mouseUp(with event: NSEvent) {
        guard urls.count > 1 else { return }
        let point = convert(event.locationInWindow, from: nil)
        if slider.frame.contains(point) {
            return
        }
        playing.toggle()
        if playing {
            scheduleNext()
        } else {
            timer?.cancel()
            timer = nil
        }
    }

    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 123:
            step(-1)
        case 124:
            step(1)
        default:
            super.keyDown(with: event)
        }
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window == nil {
            stop()
        } else {
            window?.makeFirstResponder(self)
        }
    }

    @objc private func scrubbed(_ sender: NSSlider) {
        playing = false
        timer?.cancel()
        timer = nil
        let next = min(max(Int(sender.doubleValue.rounded()), 0), max(urls.count - 1, 0))
        show(index: next)
    }

    private func step(_ delta: Int) {
        guard !urls.isEmpty else { return }
        playing = false
        timer?.cancel()
        timer = nil
        let next = (index + delta + urls.count) % urls.count
        slider.doubleValue = Double(next)
        show(index: next)
    }

    private func show(index: Int) {
        guard urls.indices.contains(index) else { return }
        generation += 1
        let token = generation
        let url = urls[index]
        let pixels = maxPixel
        queue.async { [weak self] in
            let image = self?.decode(url, maxPixel: pixels)
            DispatchQueue.main.async {
                guard let self, token == self.generation else { return }
                self.index = index
                self.image = image ?? PreviewRenderer.errorImage(message: "Could not read this frame.")
                self.caption = self.playlist?.caption(at: index) ?? ""
                self.slider.doubleValue = Double(index)
                self.needsDisplay = true
                if self.playing {
                    self.scheduleNext()
                }
            }
        }
    }

    private func scheduleNext() {
        timer?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + frameDelay)
        timer.setEventHandler { [weak self] in
            DispatchQueue.main.async {
                guard let self, self.playing, self.urls.count > 1 else { return }
                let next = (self.index + 1) % self.urls.count
                self.show(index: next)
            }
        }
        self.timer = timer
        timer.resume()
    }

    private func decode(_ url: URL, maxPixel: Int) -> CGImage? {
        switch PreviewRenderer.frame(url: url, maxPixel: maxPixel) {
        case .decline:
            return nil
        case .image(let image, _):
            return image
        }
    }
}
