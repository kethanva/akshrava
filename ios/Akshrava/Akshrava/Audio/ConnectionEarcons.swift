//
//  ConnectionEarcons.swift
//  Akshrava iOS
//

import Foundation
import AudioToolbox

public class ConnectionEarcons {
    public static func playOpen() {
        AudioServicesPlaySystemSound(1054) // System tone for open
    }
    
    public static func playDropped() {
        AudioServicesPlaySystemSound(1053) // System tone for drop
    }
    
    public static func playRestored() {
        AudioServicesPlaySystemSound(1052) // System tone for restore
    }
}
