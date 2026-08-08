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

    private init() {}

    /// Register the headset double-press (toggle-play/pause) as a mute trigger.
    /// MPRemoteCommandCenter is iOS-only; on macOS this is a no-op.
    public func setup() {
        #if os(iOS)
        let commandCenter = MPRemoteCommandCenter.shared()
        commandCenter.togglePlayPauseCommand.addTarget { _ in
            AlertManager.shared.toggleMute()
            return .success
        }
        #endif
    }
}
