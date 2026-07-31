package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * F-71 ambient light edge context. Exercises the pure state machine only: the sensor wrapper
 * around it holds nothing but a registration.
 */
class AmbientLightMonitorTest {

    private fun run(
        samples: List<Pair<Float, Long>>,
        initial: AmbientLightState = AmbientLightState()
    ): Pair<AmbientLightState, List<Pair<AmbientLightLevel, Long>>> {
        var state = initial
        val announced = mutableListOf<Pair<AmbientLightLevel, Long>>()
        for ((lux, atMs) in samples) {
            val stepped = AmbientLightMonitor.step(state, lux, atMs)
            state = stepped.state
            stepped.announce?.let { announced += it to atMs }
        }
        return state to announced
    }

    @Test
    fun classifyOnlyCommitsOutsideTheHysteresisBand() {
        assertEquals(AmbientLightLevel.DARK, AmbientLightMonitor.classify(0f))
        assertEquals(AmbientLightLevel.DARK, AmbientLightMonitor.classify(3f))
        assertEquals(AmbientLightLevel.BRIGHT, AmbientLightMonitor.classify(400f))

        // Indoor office / overcast dusk: real readings sit here for minutes at a time. Committing
        // to a level inside the band would make the monitor chatter across a single threshold.
        assertNull(AmbientLightMonitor.classify(AmbientLightMonitor.DARK_LUX))
        assertNull(AmbientLightMonitor.classify(25f))
        assertNull(AmbientLightMonitor.classify(AmbientLightMonitor.BRIGHT_LUX))
    }

    @Test
    fun firstConfidentReadingSeedsWithoutSpeaking() {
        // Starting a walk indoors is not an edge. Announcing here would greet every session with
        // a line about light the user did not ask for.
        val (state, announced) = run(listOf(2f to 0L, 2f to 1_000L, 2f to 9_000L))

        assertEquals(AmbientLightLevel.DARK, state.established)
        assertEquals(emptyList<Pair<AmbientLightLevel, Long>>(), announced)
    }

    @Test
    fun sustainedDarkToBrightEdgeAnnouncesOnceAfterHold() {
        val (state, announced) = run(
            listOf(
                2f to 0L,                                             // seed DARK
                800f to 1_000L,                                       // candidate opens
                800f to 1_000L + AmbientLightMonitor.HOLD_MS - 1,     // still short of the hold
                800f to 1_000L + AmbientLightMonitor.HOLD_MS,         // confirmed
                800f to 20_000L                                       // already established: silent
            )
        )

        assertEquals(
            listOf(AmbientLightLevel.BRIGHT to 1_000L + AmbientLightMonitor.HOLD_MS),
            announced
        )
        assertEquals(AmbientLightLevel.BRIGHT, state.established)
        assertNull(state.candidate)
    }

    @Test
    fun brightToDarkEdgeAnnouncesDark() {
        val seeded = AmbientLightState(established = AmbientLightLevel.BRIGHT)
        val (_, announced) = run(
            listOf(1f to 0L, 1f to AmbientLightMonitor.HOLD_MS),
            initial = seeded
        )

        assertEquals(listOf(AmbientLightLevel.DARK to AmbientLightMonitor.HOLD_MS), announced)
    }

    @Test
    fun briefFlickerShorterThanHoldNeverSpeaks() {
        // Walking under an awning, past a shop window, or under a streetlight. These are the
        // common case, and a prompt for each one turns the audio channel into noise.
        val (state, announced) = run(
            listOf(
                2f to 0L,
                900f to 1_000L,
                900f to 1_500L,
                2f to 2_000L,
                900f to 2_500L,
                2f to 3_000L
            )
        )

        assertEquals(emptyList<Pair<AmbientLightLevel, Long>>(), announced)
        assertEquals(AmbientLightLevel.DARK, state.established)
        assertNull(state.candidate)
    }

    @Test
    fun bandReadingsNeitherSeedNorBreakAPendingEdge() {
        val (state, announced) = run(listOf(25f to 0L, 30f to 5_000L))

        // Nothing confident was ever seen, so there is nothing to compare a later edge against.
        assertNull(state.established)
        assertEquals(emptyList<Pair<AmbientLightLevel, Long>>(), announced)
    }

    @Test
    fun aSlowRampThroughTheBandStillResolvesToAnEdge() {
        val (_, announced) = run(
            listOf(
                2f to 0L,                                          // seed DARK
                200f to 1_000L,                                    // candidate BRIGHT opens
                30f to 2_000L,                                     // band: neutral, holds the candidate
                200f to 1_000L + AmbientLightMonitor.HOLD_MS       // confirmed
            )
        )

        assertEquals(
            listOf(AmbientLightLevel.BRIGHT to 1_000L + AmbientLightMonitor.HOLD_MS),
            announced
        )
    }

    @Test
    fun edgeInsideTheCooldownIsSwallowedButStillAdopted() {
        val hold = AmbientLightMonitor.HOLD_MS
        val (state, announced) = run(
            listOf(
                2f to 0L,                    // seed DARK
                900f to 100L,
                900f to 100L + hold,         // announce BRIGHT
                2f to 200L + hold,
                2f to 200L + 2 * hold        // edge back to DARK, inside the 8 s cooldown
            )
        )

        assertEquals(1, announced.size)
        assertEquals(AmbientLightLevel.BRIGHT, announced.single().first)
        // Adopted anyway: leaving `established` on BRIGHT would make the very next dark sample
        // look like a fresh edge and fire the moment the cooldown lapses.
        assertEquals(AmbientLightLevel.DARK, state.established)
        assertNull(state.candidate)
    }

    @Test
    fun aLaterEdgeSpeaksOnceTheCooldownHasLapsed() {
        val hold = AmbientLightMonitor.HOLD_MS
        val cooldown = AmbientLightMonitor.COOLDOWN_MS
        val (_, announced) = run(
            listOf(
                2f to 0L,
                900f to 100L,
                900f to 100L + hold,                       // announce BRIGHT
                2f to 100L + hold + cooldown,
                2f to 100L + 2 * hold + cooldown           // announce DARK
            )
        )

        assertEquals(
            listOf(AmbientLightLevel.BRIGHT, AmbientLightLevel.DARK),
            announced.map { it.first }
        )
    }

    @Test
    fun cooldownIsAtLeastTheEightSecondsTheSpecCallsFor() {
        // Guards the constant itself: this prompt shares the user's only audio channel with
        // hazard alerts, so shortening it is a decision, not a tweak.
        org.junit.Assert.assertTrue(AmbientLightMonitor.COOLDOWN_MS >= 8_000L)
    }
}
