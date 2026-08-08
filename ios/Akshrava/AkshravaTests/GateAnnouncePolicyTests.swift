import XCTest
@testable import Akshrava

final class GateAnnouncePolicyTests: XCTestCase {

    func testOcclusionRequiresConsecutiveFrames() {
        XCTAssertFalse(
            AssistSessionManager.shouldAnnounceOcclusion(consecutive: 2, nowMs: 1_000, lastAnnounceMs: nil)
        )
        XCTAssertTrue(
            AssistSessionManager.shouldAnnounceOcclusion(consecutive: 3, nowMs: 1_000, lastAnnounceMs: nil)
        )
    }

    func testGlareRequiresConsecutiveFramesAndCooldown() {
        XCTAssertTrue(
            AssistSessionManager.shouldAnnounceGlare(consecutive: 3, nowMs: 10_000, lastAnnounceMs: nil)
        )
        XCTAssertFalse(
            AssistSessionManager.shouldAnnounceGlare(consecutive: 3, nowMs: 12_000, lastAnnounceMs: 10_000)
        )
        XCTAssertTrue(
            AssistSessionManager.shouldAnnounceGlare(
                consecutive: 3,
                nowMs: 10_000 + AssistSessionManager.gateAnnounceCooldownMs,
                lastAnnounceMs: 10_000
            )
        )
    }

    func testGestureDebounce() {
        XCTAssertTrue(GestureDetectorEngine.shouldFire(nowMs: 1_000, lastTriggerMs: 0))
        XCTAssertFalse(GestureDetectorEngine.shouldFire(nowMs: 1_200, lastTriggerMs: 1_000))
        XCTAssertTrue(
            GestureDetectorEngine.shouldFire(
                nowMs: 1_000 + GestureDetectorEngine.debounceMs,
                lastTriggerMs: 1_000
            )
        )
    }
}
