//
//  AgentDebugLog.swift
//  Akshrava iOS
//

import Foundation
import os

public struct AgentDebugLog {
    private static let logger = Logger(subsystem: "org.akshrava.ios", category: "agent")

    /// DEBUG-only (AKSHRAVA_DEBUG, defined by Package.swift's swiftSettings). This is called on
    /// the hot frame path (a `frame_drop` line per dropped frame, `stale_inference_tick`, send
    /// failures that can embed a URL) via `print`, which is synchronous and unbuffered -- in a
    /// release build that cost was paid on every frame for a log line no operator would ever
    /// read. os.Logger with a DEBUG gate matches the pattern already established for the debug
    /// provisioning bundle in ProvisionStore.
    public static func log(message: String) {
        #if AKSHRAVA_DEBUG
        logger.debug("\(message, privacy: .public)")
        #endif
    }
}
