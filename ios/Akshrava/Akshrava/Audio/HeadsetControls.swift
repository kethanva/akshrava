//
//  HeadsetControls.swift
//  Akshrava iOS
//

import Foundation
#if os(iOS)
import MediaPlayer
#endif

public class HeadsetControls {
    public static let shared = HeadsetControls()
    private let stateLock = NSLock()
    private var isConfigured = false

    private init() {}

    /// Register the headset double-press (next-track command) as a mute trigger.
    /// MPRemoteCommandCenter is iOS-only; on macOS this is a no-op.
    public func setup() {
        #if os(iOS)
        stateLock.lock()
        guard !isConfigured else {
            stateLock.unlock()
            return
        }
        isConfigured = true
        stateLock.unlock()
        // Remote commands are not reliably delivered to an app that neither publishes Now
        // Playing metadata nor opts into remote-control events. This is the intentional,
        // accessible mute control, so make its registration real rather than a best-effort hook.
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [
            MPMediaItemPropertyTitle: "Akshrava assistance"
        ]
        let commandCenter = MPRemoteCommandCenter.shared()
        // iOS translates a headset double-press into the media "next track" command. Binding
        // togglePlayPause instead made a single accidental press mute the app, violating the
        // deliberate-double-press-only contract for the one action allowed to silence speech.
        commandCenter.nextTrackCommand.isEnabled = true
        commandCenter.nextTrackCommand.addTarget { _ in
            AlertManager.shared.toggleMute()
            return .success
        }
        #endif
    }
}
