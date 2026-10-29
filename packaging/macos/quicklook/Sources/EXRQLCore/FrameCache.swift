import Foundation

/// Decoded frames kept for looping playback, bounded by bytes.
///
/// Frames are kept in play order from the playhead. When the budget is full,
/// the frame furthest ahead in play order (the one just played, on a loop) is
/// dropped first, so the frames about to play stay cached. Not thread-safe;
/// the flipbook uses it from the main thread.
struct FrameCache<Value> {
    let budget: Int
    private(set) var bytes = 0
    private var entries: [Int: (value: Value, cost: Int)] = [:]

    init(budget: Int) {
        self.budget = max(budget, 1)
    }

    var count: Int { entries.count }
    var indices: [Int] { Array(entries.keys) }

    func value(at index: Int) -> Value? {
        entries[index]?.value
    }

    func contains(_ index: Int) -> Bool {
        entries[index] != nil
    }

    /// Frames from ``playhead`` to ``index`` in play order, wrapping at ``count``.
    static func distance(_ index: Int, from playhead: Int, count: Int) -> Int {
        guard count > 0 else { return 0 }
        return ((index - playhead) % count + count) % count
    }

    /// Insert ``value`` and evict to stay within budget. Returns false when the
    /// new frame was the furthest ahead and was dropped again.
    @discardableResult
    mutating func insert(_ value: Value, cost: Int, at index: Int, playhead: Int, count: Int) -> Bool {
        if let old = entries[index] {
            bytes -= old.cost
        }
        entries[index] = (value, max(cost, 0))
        bytes += max(cost, 0)
        while bytes > budget, entries.count > 1 {
            guard let victim = furthest(playhead: playhead, count: count) else { break }
            remove(victim)
            if victim == index {
                return false
            }
        }
        return true
    }

    mutating func remove(_ index: Int) {
        if let old = entries.removeValue(forKey: index) {
            bytes -= old.cost
        }
    }

    mutating func removeAll() {
        entries.removeAll()
        bytes = 0
    }

    /// The next frame to decode in play order, or nil when everything ahead is
    /// cached or the cache cannot take a frame that close to the playhead.
    func nextToDecode(playhead: Int, count: Int, busy: Set<Int>, estimate: Int) -> Int? {
        guard count > 0 else { return nil }
        let furthestCached = furthest(playhead: playhead, count: count)
            .map { Self.distance($0, from: playhead, count: count) }
        let projected = bytes + max(estimate, 1) * (busy.count + 1)
        for step in 0..<count {
            let index = (playhead + step) % count
            if entries[index] != nil || busy.contains(index) {
                continue
            }
            if projected <= budget {
                return index
            }
            if let furthestCached, furthestCached > step {
                return index
            }
            return nil
        }
        return nil
    }

    private func furthest(playhead: Int, count: Int) -> Int? {
        var best: Int?
        var bestDistance = -1
        for index in entries.keys {
            let d = Self.distance(index, from: playhead, count: count)
            if d > bestDistance {
                bestDistance = d
                best = index
            }
        }
        return best
    }
}
