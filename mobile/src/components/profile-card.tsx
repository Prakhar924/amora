/**
 * The discovery card and its swipe gesture.
 *
 * Implementation notes:
 *
 * * The gesture runs on the UI thread via Reanimated worklets, so dragging
 *   stays at 60fps even while a network request is in flight.
 * * A swipe commits past either a distance threshold or a velocity threshold,
 *   so a quick flick works as well as a deliberate drag.
 * * `onDecision` is called from `runOnJS` once the exit animation finishes,
 *   which keeps the parent's list mutation off the gesture path.
 * * Photos are paged by tapping the left or right half of the card, the
 *   convention these apps have settled on.
 */

import { useCallback, useState } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, {
  interpolate,
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withSpring,
  withTiming,
} from 'react-native-reanimated';

import type { PublicProfile, SwipeAction } from '@/lib/types';

// Fractions rather than pixels: the pixel thresholds are derived per render
// from the live window size (see `useWindowDimensions` below). Reading
// `Dimensions.get('window')` at module scope returns 0 during the first web
// render, which collapses the interpolation range and pins the card at its
// maximum rotation.
const SWIPE_DISTANCE_FRACTION = 0.28;
const SWIPE_VELOCITY_THRESHOLD = 800;
const EXIT_DURATION = 220;
const MAX_TILT_DEGREES = 12;

interface Props {
  profile: PublicProfile;
  onDecision: (action: SwipeAction) => void;
  onOpenProfile?: () => void;
  /** Only the top card is interactive; the one beneath is decorative. */
  interactive?: boolean;
}

export function ProfileCard({
  profile,
  onDecision,
  onOpenProfile,
  interactive = true,
}: Props) {
  // Live window width: correct on first web render, and it follows rotation
  // and browser resizing, which a module-level constant never would.
  const { width: screenWidth } = useWindowDimensions();
  const swipeThreshold = screenWidth * SWIPE_DISTANCE_FRACTION;

  const translateX = useSharedValue(0);
  const translateY = useSharedValue(0);
  const [photoIndex, setPhotoIndex] = useState(0);

  const photos = profile.photos;
  const photo = photos[photoIndex] ?? photos[0];

  const commit = useCallback(
    (action: SwipeAction) => {
      onDecision(action);
    },
    [onDecision],
  );

  const pan = Gesture.Pan()
    .enabled(interactive)
    .onUpdate((event) => {
      translateX.value = event.translationX;
      // Damped vertical movement: the card should tilt and follow, not fly up.
      translateY.value = event.translationY * 0.25;
    })
    .onEnd((event) => {
      const passedDistance = Math.abs(translateX.value) > swipeThreshold;
      const passedVelocity = Math.abs(event.velocityX) > SWIPE_VELOCITY_THRESHOLD;

      if (passedDistance || passedVelocity) {
        const liked = translateX.value > 0 || event.velocityX > 0;
        // Fling the card off-screen, then notify the parent.
        translateX.value = withTiming(
          liked ? screenWidth * 1.5 : -screenWidth * 1.5,
          { duration: EXIT_DURATION },
          (finished) => {
            if (finished) runOnJS(commit)(liked ? 'like' : 'pass');
          },
        );
        return;
      }

      // Not far enough: spring back.
      translateX.value = withSpring(0, { damping: 18, stiffness: 180 });
      translateY.value = withSpring(0, { damping: 18, stiffness: 180 });
    });

  const cardStyle = useAnimatedStyle(() => {
    // Guard the degenerate case: an input range of [0, 0, 0] makes
    // `interpolate` return an endpoint rather than 0.
    const span = screenWidth > 0 ? screenWidth : 1;
    return {
      transform: [
        { translateX: translateX.value },
        { translateY: translateY.value },
        {
          rotate: `${interpolate(
            translateX.value,
            [-span, 0, span],
            [-MAX_TILT_DEGREES, 0, MAX_TILT_DEGREES],
          )}deg`,
        },
      ],
    };
  });

  // The LIKE / NOPE stamps fade in as the card is dragged.
  const likeStyle = useAnimatedStyle(() => ({
    opacity: interpolate(
      translateX.value,
      [0, swipeThreshold > 0 ? swipeThreshold : 1],
      [0, 1],
    ),
  }));
  const nopeStyle = useAnimatedStyle(() => ({
    opacity: interpolate(
      translateX.value,
      [swipeThreshold > 0 ? -swipeThreshold : -1, 0],
      [1, 0],
    ),
  }));

  function tapPhoto(side: 'left' | 'right') {
    if (photos.length < 2) {
      onOpenProfile?.();
      return;
    }
    setPhotoIndex((current) => {
      if (side === 'right') return Math.min(photos.length - 1, current + 1);
      return Math.max(0, current - 1);
    });
  }

  return (
    <GestureDetector gesture={pan}>
      <Animated.View
        // Layout is set here rather than through `className`: NativeWind's
        // generated styles lose to the `style` prop on an Animated.View, which
        // silently collapsed the card's height.
        style={[
          StyleSheet.absoluteFill,
          { borderRadius: 24, overflow: 'hidden' },
          cardStyle,
        ]}
        className="bg-ink-200 dark:bg-ink-800"
      >
        {photo ? (
          <Image
            source={{ uri: photo.url }}
            style={{ position: 'absolute', width: '100%', height: '100%' }}
            contentFit="cover"
            transition={180}
            // Signed URLs expire, so caching them by URL would serve stale
            // links; memory-only caching avoids persisting a dead URL.
            cachePolicy="memory"
          />
        ) : (
          <View className="flex-1 items-center justify-center">
            <Text className="text-6xl">🌸</Text>
          </View>
        )}

        {/* Photo pagination dots */}
        {photos.length > 1 ? (
          <View className="absolute top-3 left-3 right-3 flex-row gap-1.5">
            {photos.map((item, index) => (
              <View
                key={item.id}
                className={`flex-1 h-1 rounded-pill ${
                  index === photoIndex ? 'bg-white' : 'bg-white/35'
                }`}
              />
            ))}
          </View>
        ) : null}

        {/* Tap targets for paging through photos */}
        <View className="absolute inset-0 flex-row">
          <Pressable
            className="flex-1"
            onPress={() => tapPhoto('left')}
            accessibilityLabel="Previous photo"
          />
          <Pressable
            className="flex-1"
            onPress={() => tapPhoto('right')}
            accessibilityLabel="Next photo"
          />
        </View>

        {/* Scrim so white text stays readable over any photo */}
        <View className="absolute bottom-0 left-0 right-0 h-2/5 bg-black/55" />

        <Pressable
          onPress={onOpenProfile}
          className="absolute bottom-0 left-0 right-0 p-5"
          accessibilityLabel={`View ${profile.display_name}'s full profile`}
        >
          <View className="flex-row items-end gap-2 mb-1">
            <Text className="font-display text-3xl text-white">
              {profile.display_name}
            </Text>
            <Text className="text-2xl text-white/90 mb-0.5">{profile.age}</Text>
          </View>

          <View className="flex-row items-center gap-2 flex-wrap">
            {profile.job_title ? (
              <Text className="text-white/85 text-sm">{profile.job_title}</Text>
            ) : null}
            {profile.distance_km !== null ? (
              <Text className="text-white/70 text-sm">
                · {profile.distance_km} km away
              </Text>
            ) : profile.city ? (
              <Text className="text-white/70 text-sm">· {profile.city}</Text>
            ) : null}
          </View>

          {profile.bio ? (
            <Text className="text-white/90 text-sm mt-2 leading-5" numberOfLines={2}>
              {profile.bio}
            </Text>
          ) : null}

          {profile.interests.length > 0 ? (
            <View className="flex-row flex-wrap gap-1.5 mt-3">
              {profile.interests.slice(0, 3).map((interest) => (
                <View
                  key={interest.slug}
                  className="rounded-pill bg-white/20 px-3 py-1"
                >
                  <Text className="text-white text-xs">{interest.label}</Text>
                </View>
              ))}
            </View>
          ) : null}
        </Pressable>

        {/* Drag stamps */}
        <Animated.View
          style={likeStyle}
          className="absolute top-12 left-6 border-4 border-success rounded-2xl px-4 py-1.5 -rotate-12"
        >
          <Text className="text-success font-bold text-3xl tracking-wider">
            LIKE
          </Text>
        </Animated.View>
        <Animated.View
          style={nopeStyle}
          className="absolute top-12 right-6 border-4 border-danger rounded-2xl px-4 py-1.5 rotate-12"
        >
          <Text className="text-danger font-bold text-3xl tracking-wider">
            NOPE
          </Text>
        </Animated.View>
      </Animated.View>
    </GestureDetector>
  );
}
