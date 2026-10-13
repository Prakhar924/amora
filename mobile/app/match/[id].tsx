/**
 * The match celebration, shown as a modal immediately after a mutual like.
 *
 * Deliberately offers two exits: start the conversation now, or keep swiping.
 * Forcing the user into a chat is a worse experience than letting them
 * continue and come back to it.
 */

import { useEffect } from 'react';
import { Pressable, Text, View } from 'react-native';
import { Image } from 'expo-image';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';

import { Button } from '@/components/ui';

export default function MatchCelebration() {
  const { id, name, photo, conversationId } = useLocalSearchParams<{
    id: string;
    name?: string;
    photo?: string;
    conversationId?: string;
  }>();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const scale = useSharedValue(0.6);
  const opacity = useSharedValue(0);
  const heartScale = useSharedValue(0);

  useEffect(() => {
    opacity.value = withTiming(1, { duration: 280 });
    scale.value = withSpring(1, { damping: 12, stiffness: 140 });
    // A small overshoot gives the moment a bit of delight.
    heartScale.value = withDelay(
      180,
      withSequence(
        withSpring(1.25, { damping: 8 }),
        withSpring(1, { damping: 10 }),
      ),
    );
  }, [opacity, scale, heartScale]);

  const cardStyle = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ scale: scale.value }],
  }));
  const heartStyle = useAnimatedStyle(() => ({
    transform: [{ scale: heartScale.value }],
  }));

  return (
    <View className="flex-1 bg-ink-900">
      <View className="absolute inset-0 bg-rose-900 opacity-50" />
      <View className="absolute -top-20 -left-20 w-72 h-72 rounded-full bg-rose-500 opacity-30" />
      <View className="absolute bottom-10 -right-24 w-80 h-80 rounded-full bg-plum-500 opacity-30" />

      <View
        className="flex-1 items-center justify-center px-8"
        style={{ paddingTop: insets.top, paddingBottom: insets.bottom + 24 }}
      >
        <Animated.View style={heartStyle}>
          <Text className="text-6xl mb-4">💞</Text>
        </Animated.View>

        <Text className="font-display text-4xl text-white text-center mb-2">
          It&apos;s a match!
        </Text>
        <Text className="text-ink-200 text-center text-base mb-8 leading-6">
          You and {name ?? 'they'} liked each other.
        </Text>

        <Animated.View
          style={cardStyle}
          className="w-44 h-56 rounded-card overflow-hidden bg-ink-700 mb-10"
        >
          {photo ? (
            <Image
              source={{ uri: photo }}
              style={{ width: '100%', height: '100%' }}
              contentFit="cover"
              cachePolicy="memory"
            />
          ) : (
            <View className="flex-1 items-center justify-center">
              <Text className="text-5xl">🌸</Text>
            </View>
          )}
        </Animated.View>

        <View className="w-full gap-3">
          <Button
            label={`Message ${name ?? 'them'}`}
            onPress={() => {
              if (conversationId) {
                router.replace({
                  pathname: '/chat/[id]',
                  params: { id: conversationId, name: name ?? '' },
                });
              } else {
                router.replace('/(tabs)/chats');
              }
            }}
          />
          <Pressable onPress={() => router.back()} className="py-3">
            <Text className="text-center text-ink-200 font-medium">
              Keep swiping
            </Text>
          </Pressable>
        </View>
      </View>
    </View>
  );
}
