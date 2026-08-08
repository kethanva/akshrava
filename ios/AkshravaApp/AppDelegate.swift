//
//  AppDelegate.swift — installable UIKit host for the Akshrava SPM library.
//

import UIKit
import Akshrava

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        application.beginReceivingRemoteControlEvents()
        // Register background tasks FIRST — Apple requires this before any async work.
        Watchdog.shared.registerBackgroundTasks()
        // Headset double-press → mute toggle (mirrors Android headset broadcast receiver).
        HeadsetControls.shared.setup()

        _ = ProvisionStore.loadFromBundleProvisionJSON()

        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = MainViewController()
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
}
