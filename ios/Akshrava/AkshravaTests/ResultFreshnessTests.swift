import XCTest
@testable import Akshrava

final class ResultFreshnessTests: XCTestCase {

    func testMissingCaptureMonoIsNeverFresh() {
        let age = ProtocolClient.resultAgeMs(captureMonoMs: nil, nowMs: 10_000)
        XCTAssertGreaterThan(age, ProtocolClient.staleAlertMs)
    }

    func testFreshResultWithinConfiguredBudget() {
        let capture: Int64 = 8_000
        let now: Int64 = 10_000
        let age = ProtocolClient.resultAgeMs(captureMonoMs: capture, nowMs: now)
        XCTAssertEqual(age, 2_000)
        let maxAge = ProtocolClient.maxSpeakAgeMs(
            priority: false,
            isUrgent: false,
            configuredStaleAlertMs: ProtocolClient.staleAlertMs
        )
        XCTAssertTrue(age <= maxAge)
    }

    func testStaleNormalResultIsSuppressed() {
        let age = ProtocolClient.resultAgeMs(captureMonoMs: 1_000, nowMs: 10_000)
        let maxAge = ProtocolClient.maxSpeakAgeMs(
            priority: false,
            isUrgent: false,
            configuredStaleAlertMs: ProtocolClient.staleAlertMs
        )
        XCTAssertGreaterThan(age, maxAge)
    }

    func testUrgentUsesTighterBudget() {
        let urgent = ProtocolClient.maxSpeakAgeMs(
            priority: false,
            isUrgent: true,
            configuredStaleAlertMs: ProtocolClient.staleAlertMs
        )
        XCTAssertEqual(urgent, max(ProtocolClient.urgentFreshnessMs, ProtocolClient.staleAlertMs))
    }

    func testPriorityUsesLookBudget() {
        let look = ProtocolClient.maxSpeakAgeMs(
            priority: true,
            isUrgent: true,
            configuredStaleAlertMs: ProtocolClient.staleAlertMs
        )
        XCTAssertEqual(look, max(ProtocolClient.lookFreshnessMs, ProtocolClient.staleAlertMs))
    }
}
