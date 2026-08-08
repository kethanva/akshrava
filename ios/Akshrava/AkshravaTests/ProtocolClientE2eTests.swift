//
//  ProtocolClientE2eTests.swift
//  AkshravaTests
//

import XCTest
@testable import Akshrava

final class ProtocolClientE2eTests: XCTestCase {

    private static let fixtureJpeg: Data = {
        let b64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoH" +
                  "BwYIDAoMCwsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQME" +
                  "BAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQU" +
                  "FBQUFBT/wAARCAAQABADASIAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAAAAAABgUH/8QA" +
                  "IhAAAQMEAwEBAAAAAAAAAAAAAQIDBAUREiExUf/EABQBAQAAAAAAAAAAAAAAAAAAAAD/" +
                  "xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwDKaZ0VVGkpVJS5EEJFTqp" +
                  "SmCdFVRpKVSUuRBCRU6qUpgnRVUaSlUlLkQQkVOqlKYJ0VVGkpVJS5EEJFT//2Q=="
        return Data(base64Encoded: b64, options: .ignoreUnknownCharacters) ?? Data()
    }()

    func testLiveGcpProtocolClientEndToEnd() throws {
        let wssUrl = ProcessInfo.processInfo.environment["AKSHRAVA_WSS_URL"] ?? ""
        let token = ProcessInfo.processInfo.environment["AKSHRAVA_TEST_TOKEN"] ?? ""

        guard !wssUrl.isEmpty && !token.isEmpty else {
            throw XCTSkip("Skipping live E2E: set AKSHRAVA_WSS_URL + AKSHRAVA_TEST_TOKEN")
        }

        guard let url = URL(string: wssUrl) else {
            XCTFail("AKSHRAVA_WSS_URL is not a valid URL: \(wssUrl)")
            return
        }

        let connectExpectation = expectation(description: "WebSocket connection established")
        let client = ProtocolClient()

        final class TestDelegate: NSObject, ProtocolClientDelegate {
            let connectExpectation: XCTestExpectation
            var connected = false

            init(exp: XCTestExpectation) {
                self.connectExpectation = exp
            }

            func protocolClientDidConnect(_ client: ProtocolClient) {
                connected = true
                connectExpectation.fulfill()
            }
            func protocolClient(_ client: ProtocolClient, didDisconnectWithCode code: Int, reason: String?) {}
            func protocolClient(_ client: ProtocolClient, didReceiveResult payload: [String: Any]) {}
            func protocolClient(_ client: ProtocolClient, didReceiveQuality qualityPayload: [String: Any]) {}
            func protocolClient(_ client: ProtocolClient, didReceiveError errorPayload: [String: Any]) {}
        }

        let delegate = TestDelegate(exp: connectExpectation)
        client.delegate = delegate
        client.connect(url: url, authToken: token)

        wait(for: [connectExpectation], timeout: 30.0)
        XCTAssertTrue(delegate.connected, "ProtocolClient must receive vision-ready from server")

        let frame = EncodedFrameWire(jpegData: Self.fixtureJpeg, width: 64, height: 64)
        _ = client.sendFrame(
            frameId: 1,
            captureMonoMs: Int64(ProcessInfo.processInfo.systemUptime * 1000),
            pitchCdeg: 0,
            rollCdeg: 0,
            poseAgeMs: 100,
            frame: frame,
            calibrationId: "e2e_ios"
        )

        client.disconnect()
    }

    func testPoseBelowLegacyFloorIsOmittedNotClampedMax() {
        XCTAssertNil(ProtocolClient.wirePoseCdeg(ProtocolClient.legacyPoseCdegFloor - 1))
        XCTAssertEqual(ProtocolClient.legacyPoseCdegFloor, -9000)
    }
}
