import Cocoa
import Foundation

/// A frame held in the flipbook cache.
private struct CachedFrame {
    enum Pixels {
        /// Scene-linear, log-encoded. View, exposure and gamma apply at draw time.
        case scene(SceneFrame)
        /// Already display-encoded.
        case display(CGImage)
    }

    let pixels: Pixels
    let sourceSize: CGSize
    let input: InputDetection?

    var cost: Int {
        switch pixels {
        case .scene(let frame):
            return frame.byteCount
        case .display(let image):
            return image.bytesPerRow * image.height
        }
    }

    var isScene: Bool {
        if case .scene = pixels { return true }
        return false
    }
}

/// Custom preview surface, laid out like the app's player: exposure, gamma,
/// input and view controls on top, the picture, then transport buttons and a
/// frame ruler with a cache bar.
///
/// Numbered frames play at 24 fps. Frames are decoded ahead of the playhead on
/// several threads and kept in memory (up to an eighth of RAM, at most 1 GB),
/// so a loop that fits plays from memory. Scene-linear frames are cached
/// log-encoded, so changing the view, exposure or gamma redraws without
/// decoding again. One frame, or a folder the sandbox cannot list, shows the
/// selected file.
final class FlipbookView: NSView {
    private static let viewDefaultsKey = "viewTitle"
    private static let topHeight: CGFloat = 30
    private static let bottomHeight: CGFloat = 40
    private static let labelHeight: CGFloat = 22

    // Playback and cache.
    private var playlist: SequencePlaylist?
    private var urls: [URL] = []
    private var format: QuickLookFormat?
    private var index = 0
    private var playing = false
    private var maxPixel = 1920
    private let frameRate: Double = 24
    private var cache = FrameCache<CachedFrame>(budget: FlipbookView.cacheBudget())
    private var busy = Set<Int>()
    private var wanted: Int?
    private var generation = 0
    private var lastCost = 4 << 20
    private var timer: DispatchSourceTimer?
    private let decodeQueue = DispatchQueue(
        label: "com.vfxtools.exrconverter.quicklook.decode",
        qos: .userInitiated,
        attributes: .concurrent
    )
    private let maxBusy = max(2, min(6, ProcessInfo.processInfo.activeProcessorCount - 2))

    // Color.
    private var choices: [ViewChoice] = []
    private var choiceIndex = 0
    private var look = ViewerLook()
    private var inputOverride: String?
    private var inputNames: [String] = []
    private var showsColorControls = false

    // Drawing.
    private var shownFrame: CachedFrame?
    private var shownImage: CGImage?
    private var caption = ""

    // Controls.
    private let gainButton = FlipbookView.valueButton()
    private let gainSlider = NSSlider(value: 0, minValue: log2(0.01), maxValue: log2(50), target: nil, action: nil)
    private let gammaLabel = NSTextField(labelWithString: "γ")
    private let gammaButton = FlipbookView.valueButton()
    private let gammaSlider = NSSlider(value: 1, minValue: 0.2, maxValue: 4, target: nil, action: nil)
    private let inputPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let viewPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let backButton = FlipbookView.transportButton("backward.frame.fill", fallback: "◀︎", label: "Previous frame")
    private let playButton = FlipbookView.transportButton("play.fill", fallback: "▶︎", label: "Play")
    private let forwardButton = FlipbookView.transportButton("forward.frame.fill", fallback: "▶︎", label: "Next frame")
    private let timeline = TimelineView()

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        gainButton.target = self
        gainButton.action = #selector(resetGain(_:))
        gainButton.toolTip = "Exposure gain. Click to reset."
        gainSlider.target = self
        gainSlider.action = #selector(gainChanged(_:))
        gainSlider.isContinuous = true
        gainSlider.controlSize = .small
        gammaLabel.textColor = NSColor(white: 0.6, alpha: 1)
        gammaLabel.font = NSFont.systemFont(ofSize: 11)
        gammaButton.target = self
        gammaButton.action = #selector(resetGamma(_:))
        gammaButton.toolTip = "Display gamma. Click to reset."
        gammaSlider.target = self
        gammaSlider.action = #selector(gammaChanged(_:))
        gammaSlider.isContinuous = true
        gammaSlider.controlSize = .small
        for popup in [inputPopup, viewPopup] {
            popup.controlSize = .small
            popup.font = NSFont.systemFont(ofSize: NSFont.smallSystemFontSize)
            popup.target = self
        }
        inputPopup.action = #selector(inputChanged(_:))
        inputPopup.toolTip = "Input color space"
        viewPopup.action = #selector(viewChanged(_:))
        viewPopup.toolTip = "OCIO display / view"
        backButton.target = self
        backButton.action = #selector(stepBack(_:))
        playButton.target = self
        playButton.action = #selector(togglePlay(_:))
        forwardButton.target = self
        forwardButton.action = #selector(stepForward(_:))
        timeline.onScrub = { [weak self] index in self?.scrub(to: index) }
        for view in [
            gainButton, gainSlider, gammaLabel, gammaButton, gammaSlider, inputPopup, viewPopup,
            backButton, playButton, forwardButton, timeline,
        ] as [NSView] {
            addSubview(view)
        }
        updateValueLabels()
    }

    required init?(coder: NSCoder) {
        fatalError("FlipbookView is created in code")
    }

    // MARK: Opening

    func open(url: URL) {
        stop()
        generation += 1
        cache.removeAll()
        busy.removeAll()
        wanted = nil
        inputOverride = nil
        format = FormatCatalog.format(forExtension: url.pathExtension)

        let playlist = SequencePlaylist.load(url: url)
        self.playlist = playlist
        if let playlist, playlist.canPlay {
            urls = playlist.frames.indices.map { playlist.fileURL(at: $0) }
            index = playlist.index(ofFrame: playlist.identity.frame)
            maxPixel = 960
        } else {
            urls = [url]
            index = 0
            maxPixel = 1920
        }
        timeline.frames = playlist?.canPlay == true ? playlist!.frames : []
        timeline.playhead = index

        loadChoices()
        loadInputs()

        let first = Self.decode(
            urls[index], format: format, maxPixel: maxPixel, override: nil, range: encodeRange)
        lastCost = max(first.cost, 1)
        cache.insert(first, cost: first.cost, at: index, playhead: index, count: urls.count)
        showsColorControls = first.isScene
        show(first, at: index)
        syncCacheBar()
        playing = urls.count > 1
        updatePlayButton()
        needsLayout = true
        if playing {
            startTimer()
        }
        pump()
    }

    func stop() {
        playing = false
        stopTimer()
        generation += 1
    }

    private var encodeRange: (Float, Float) {
        if let lut = choices.first?.lut {
            return (lut.minExp, lut.maxExp)
        }
        return (-10, 6)
    }

    private func loadChoices() {
        choices = ViewLUTStore.bundle()?.choices ?? []
        let bundle = ViewLUTStore.bundle()
        let preferP3 = (window?.screen ?? NSScreen.main)?.canRepresent(.p3) ?? false
        choiceIndex = bundle?.defaultIndex(preferP3: preferP3) ?? 0
        if let saved = UserDefaults.standard.string(forKey: Self.viewDefaultsKey),
            let found = choices.firstIndex(where: { $0.title == saved })
        {
            choiceIndex = found
        }
        viewPopup.removeAllItems()
        viewPopup.addItems(withTitles: choices.map { $0.title })
        if choices.indices.contains(choiceIndex) {
            viewPopup.selectItem(at: choiceIndex)
        }
        viewPopup.isEnabled = choices.count > 1
    }

    private func loadInputs() {
        inputNames = InputCatalogStore.shared()?.spaces.map { $0.name } ?? []
        inputPopup.removeAllItems()
        inputPopup.addItem(withTitle: "Auto")
        inputPopup.addItems(withTitles: inputNames)
        inputPopup.selectItem(at: 0)
        inputPopup.isEnabled = !inputNames.isEmpty && format?.key != "dpx"
    }

    // MARK: Layout and drawing

    override func layout() {
        super.layout()
        let width = bounds.width
        let top = showsColorControls ? Self.topHeight : 0
        let bottom = urls.count > 1 ? Self.bottomHeight : 0

        for view in [gainButton, gainSlider, gammaLabel, gammaButton, gammaSlider, inputPopup, viewPopup] as [NSView] {
            view.isHidden = top == 0
        }
        if top > 0 {
            let y = bounds.height - top + 5
            let viewWidth = min(260, max(width * 0.26, 120))
            let inputWidth = min(180, max(width * 0.17, 90))
            let fixed: CGFloat = 8 + 34 + 10 + 12 + 34 + 10 + inputWidth + 6 + viewWidth + 8
            let sliderWidth = max((width - fixed) / 2, 40)
            var x: CGFloat = 8
            gainButton.frame = NSRect(x: x, y: y, width: 34, height: 20)
            x += 34
            gainSlider.frame = NSRect(x: x, y: y, width: sliderWidth, height: 20)
            x += sliderWidth + 10
            gammaLabel.frame = NSRect(x: x, y: y + 2, width: 12, height: 16)
            x += 12
            gammaButton.frame = NSRect(x: x, y: y, width: 34, height: 20)
            x += 34
            gammaSlider.frame = NSRect(x: x, y: y, width: sliderWidth, height: 20)
            x += sliderWidth + 10
            inputPopup.frame = NSRect(x: x, y: y - 1, width: inputWidth, height: 22)
            x += inputWidth + 6
            viewPopup.frame = NSRect(x: x, y: y - 1, width: viewWidth, height: 22)
        }

        for view in [backButton, playButton, forwardButton, timeline] as [NSView] {
            view.isHidden = bottom == 0
        }
        if bottom > 0 {
            backButton.frame = NSRect(x: 8, y: 8, width: 28, height: 24)
            playButton.frame = NSRect(x: 38, y: 8, width: 28, height: 24)
            forwardButton.frame = NSRect(x: 68, y: 8, width: 28, height: 24)
            timeline.frame = NSRect(x: 102, y: 3, width: max(width - 110, 10), height: 34)
        }
    }

    private var pictureArea: CGRect {
        let top = showsColorControls ? Self.topHeight : 0
        let bottom = urls.count > 1 ? Self.bottomHeight : 0
        let y = bottom + Self.labelHeight
        return CGRect(x: 12, y: y, width: max(bounds.width - 24, 1), height: max(bounds.height - top - y - 8, 1))
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        context.setFillColor(CGColor(gray: 0, alpha: 1))
        context.fill(bounds)
        let bar = CGColor(gray: 0.13, alpha: 1)
        if showsColorControls {
            context.setFillColor(bar)
            context.fill(CGRect(x: 0, y: bounds.height - Self.topHeight, width: bounds.width, height: Self.topHeight))
        }
        if urls.count > 1 {
            context.setFillColor(bar)
            context.fill(CGRect(x: 0, y: 0, width: bounds.width, height: Self.bottomHeight))
        }
        let area = pictureArea
        var fitted = area
        if let image = shownImage {
            fitted = PreviewRenderer.aspectFit(
                imageSize: CGSize(width: image.width, height: image.height), in: area)
            context.interpolationQuality = .high
            context.draw(image, in: fitted)
            context.setStrokeColor(CGColor(gray: 0.55, alpha: 1))
            context.setLineWidth(1)
            context.stroke(fitted.insetBy(dx: -0.5, dy: -0.5))
        }
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
            .foregroundColor: NSColor(white: 0.8, alpha: 1),
        ]
        let labelY = fitted.minY - 17
        if let size = shownFrame?.sourceSize, size.width > 0 {
            let text = "\(Int(size.width)) × \(Int(size.height))" as NSString
            let measured = text.size(withAttributes: attrs)
            text.draw(at: NSPoint(x: fitted.maxX - measured.width, y: labelY), withAttributes: attrs)
        }
        if !caption.isEmpty {
            (caption as NSString).draw(at: NSPoint(x: fitted.minX, y: labelY), withAttributes: attrs)
        }
        _ = dirtyRect
    }

    // MARK: Showing frames

    private func show(_ frame: CachedFrame, at index: Int) {
        self.index = index
        shownFrame = frame
        shownImage = render(frame)
        caption = playlist?.caption(at: min(index, max((playlist?.frames.count ?? 1) - 1, 0))) ?? ""
        if urls.count == 1 {
            caption = SequenceLabel.overlay(
                filename: urls[0].lastPathComponent, directory: nil) ?? urls[0].lastPathComponent
        }
        timeline.playhead = index
        if let input = frame.input, inputPopup.numberOfItems > 0 {
            let prefix = inputOverride == nil ? "Auto · " : "Auto"
            inputPopup.item(at: 0)?.title = inputOverride == nil ? prefix + input.name : prefix
            inputPopup.toolTip = "Input color space (\(input.source))"
        }
        needsDisplay = true
    }

    private func render(_ frame: CachedFrame) -> CGImage? {
        switch frame.pixels {
        case .display(let image):
            return image
        case .scene(let scene):
            let choice = choices.indices.contains(choiceIndex) ? choices[choiceIndex] : nil
            return scene.render(choice: choice, look: look)
                ?? PreviewRenderer.errorImage(message: "Could not apply the view.")
        }
    }

    private func redraw() {
        if let frame = shownFrame {
            shownImage = render(frame)
            needsDisplay = true
        }
    }

    // MARK: Decoding and the cache

    private static func decode(
        _ url: URL,
        format: QuickLookFormat?,
        maxPixel: Int,
        override: String?,
        range: (Float, Float)
    ) -> CachedFrame {
        func failure(_ message: String) -> CachedFrame {
            let image = PreviewRenderer.errorImage(message: message)
                ?? CGImage(
                    width: 1, height: 1, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: 4,
                    space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                    provider: CGDataProvider(data: Data(count: 4) as CFData)!,
                    decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
            return CachedFrame(pixels: .display(image), sourceSize: .zero, input: nil)
        }
        guard let format else { return failure("This file type is not previewed.") }
        do {
            let picture = try PreviewRenderer.decodePicture(
                url: url, format: format, maxPixel: maxPixel, inputOverride: override)
            switch picture.content {
            case .display(let image):
                return CachedFrame(pixels: .display(image), sourceSize: picture.sourceSize, input: nil)
            case .scene(let linear):
                let encoded = SceneFrame.encode(linear, minExp: range.0, maxExp: range.1)
                return CachedFrame(pixels: .scene(encoded), sourceSize: picture.sourceSize, input: picture.input)
            }
        } catch {
            return failure(String(describing: error))
        }
    }

    private func request(_ target: Int) {
        guard urls.indices.contains(target), !busy.contains(target), !cache.contains(target) else { return }
        busy.insert(target)
        let url = urls[target]
        let token = generation
        let pixels = maxPixel
        let override = inputOverride
        let format = self.format
        let range = encodeRange
        decodeQueue.async { [weak self] in
            let frame = FlipbookView.decode(url, format: format, maxPixel: pixels, override: override, range: range)
            DispatchQueue.main.async {
                self?.arrived(frame, at: target, token: token)
            }
        }
    }

    private func arrived(_ frame: CachedFrame, at target: Int, token: Int) {
        guard token == generation else { return }
        busy.remove(target)
        lastCost = max(frame.cost, 1)
        if wanted == target {
            wanted = nil
            show(frame, at: target)
        }
        cache.insert(frame, cost: frame.cost, at: target, playhead: index, count: urls.count)
        syncCacheBar()
        pump()
    }

    /// Keep up to ``maxBusy`` decodes running ahead of the playhead.
    private func pump() {
        guard !urls.isEmpty else { return }
        if let wanted, !busy.contains(wanted), !cache.contains(wanted) {
            request(wanted)
        }
        while busy.count < maxBusy,
            let next = cache.nextToDecode(playhead: index, count: urls.count, busy: busy, estimate: lastCost)
        {
            request(next)
        }
    }

    private func syncCacheBar() {
        timeline.cached = IndexSet(cache.indices)
    }

    private static func cacheBudget() -> Int {
        let eighth = ProcessInfo.processInfo.physicalMemory / 8
        let budget = min(UInt64(1) << 30, max(eighth, UInt64(256) << 20))
        return Int(budget)
    }

    // MARK: Playback

    private func startTimer() {
        stopTimer()
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + 1 / frameRate, repeating: 1 / frameRate, leeway: .milliseconds(2))
        timer.setEventHandler { [weak self] in
            self?.tick()
        }
        self.timer = timer
        timer.resume()
    }

    private func stopTimer() {
        timer?.setEventHandler {}
        timer?.cancel()
        timer = nil
    }

    /// Advance when the next frame is cached; otherwise hold until it arrives.
    private func tick() {
        guard playing, urls.count > 1 else { return }
        let next = (index + 1) % urls.count
        if let frame = cache.value(at: next) {
            show(frame, at: next)
        }
        pump()
    }

    private func pause() {
        playing = false
        stopTimer()
        updatePlayButton()
    }

    private func scrub(to target: Int) {
        guard urls.indices.contains(target) else { return }
        pause()
        if let frame = cache.value(at: target) {
            wanted = nil
            show(frame, at: target)
        } else {
            wanted = target
            timeline.playhead = target
        }
        pump()
    }

    private func updatePlayButton() {
        let name = playing ? "pause.fill" : "play.fill"
        if let image = NSImage(systemSymbolName: name, accessibilityDescription: playing ? "Pause" : "Play") {
            playButton.image = image
        } else {
            playButton.title = playing ? "❚❚" : "▶︎"
        }
    }

    @objc private func togglePlay(_ sender: Any?) {
        guard urls.count > 1 else { return }
        if playing {
            pause()
        } else {
            playing = true
            wanted = nil
            updatePlayButton()
            startTimer()
            pump()
        }
    }

    @objc private func stepBack(_ sender: Any?) {
        guard !urls.isEmpty else { return }
        scrub(to: (index - 1 + urls.count) % urls.count)
    }

    @objc private func stepForward(_ sender: Any?) {
        guard !urls.isEmpty else { return }
        scrub(to: (index + 1) % urls.count)
    }

    // MARK: Color controls

    private func updateValueLabels() {
        gainButton.title = Self.valueText(look.gain)
        gammaButton.title = Self.valueText(look.gamma)
        let changed = NSColor(srgbRed: 0.88, green: 0.31, blue: 0.19, alpha: 1)
        gainButton.contentTintColor = abs(look.gain - 1) < 1e-4 ? NSColor(white: 0.83, alpha: 1) : changed
        gammaButton.contentTintColor = abs(look.gamma - 1) < 1e-4 ? NSColor(white: 0.83, alpha: 1) : changed
    }

    /// Same rounding as the player's value labels.
    private static func valueText(_ value: Float) -> String {
        if abs(value - 1) < 1e-4 { return "1.0" }
        if value >= 10 { return String(format: "%.0f", value) }
        if value >= 1 { return String(format: "%.1f", value) }
        if value >= 0.1 { return String(format: "%.2f", value) }
        return String(format: "%.3f", value)
    }

    @objc private func gainChanged(_ sender: NSSlider) {
        look.gain = exp2(Float(sender.doubleValue))
        updateValueLabels()
        redraw()
    }

    @objc private func resetGain(_ sender: Any?) {
        look.gain = 1
        gainSlider.doubleValue = 0
        updateValueLabels()
        redraw()
    }

    @objc private func gammaChanged(_ sender: NSSlider) {
        look.gamma = Float(sender.doubleValue)
        updateValueLabels()
        redraw()
    }

    @objc private func resetGamma(_ sender: Any?) {
        look.gamma = 1
        gammaSlider.doubleValue = 1
        updateValueLabels()
        redraw()
    }

    @objc private func viewChanged(_ sender: NSPopUpButton) {
        let picked = sender.indexOfSelectedItem
        guard choices.indices.contains(picked) else { return }
        choiceIndex = picked
        UserDefaults.standard.set(choices[picked].title, forKey: Self.viewDefaultsKey)
        redraw()
    }

    /// A different input means different scene values, so the cache restarts.
    @objc private func inputChanged(_ sender: NSPopUpButton) {
        let picked = sender.indexOfSelectedItem
        let override: String? = picked > 0 && picked - 1 < inputNames.count ? inputNames[picked - 1] : nil
        if override == inputOverride { return }
        inputOverride = override
        if override == nil, let input = shownFrame?.input {
            sender.item(at: 0)?.title = "Auto · " + input.name
        } else {
            sender.item(at: 0)?.title = "Auto"
        }
        generation += 1
        cache.removeAll()
        busy.removeAll()
        syncCacheBar()
        wanted = index
        pump()
    }

    // MARK: Events

    override var acceptsFirstResponder: Bool { true }

    override func mouseUp(with event: NSEvent) {
        guard urls.count > 1 else { return }
        let point = convert(event.locationInWindow, from: nil)
        if pictureArea.contains(point) {
            togglePlay(nil)
        }
    }

    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 123:
            stepBack(nil)
        case 124:
            stepForward(nil)
        case 49 where urls.count > 1:
            togglePlay(nil)
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

    // MARK: Control factories

    private static func valueButton() -> NSButton {
        let button = NSButton(title: "1.0", target: nil, action: nil)
        button.isBordered = false
        button.font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        button.alignment = .right
        return button
    }

    private static func transportButton(_ symbol: String, fallback: String, label: String) -> NSButton {
        let button: NSButton
        if let image = NSImage(systemSymbolName: symbol, accessibilityDescription: label) {
            button = NSButton(image: image, target: nil, action: nil)
        } else {
            button = NSButton(title: fallback, target: nil, action: nil)
        }
        button.isBordered = false
        button.contentTintColor = NSColor(white: 0.85, alpha: 1)
        button.toolTip = label
        return button
    }
}
