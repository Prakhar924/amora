/**
 * Renderer for the Terms and Privacy screens.
 *
 * Both documents are plain structured content, so they share one presentation
 * component rather than each hand-rolling a scroll view.
 */

import { ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Button } from './ui';

export interface LegalSection {
  heading: string;
  body: string;
}

export function LegalDocument({
  title,
  updated,
  sections,
}: {
  title: string;
  updated: string;
  sections: LegalSection[];
}) {
  const insets = useSafeAreaInsets();
  const router = useRouter();

  return (
    <ScrollView
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      contentContainerClassName="px-7 pb-16"
      contentContainerStyle={{ paddingTop: insets.top + 24 }}
    >
      <Text className="font-display text-3xl text-ink-900 dark:text-ink-50 mb-1">
        {title}
      </Text>
      <Text className="text-sm text-ink-400 mb-8">Last updated {updated}</Text>

      {sections.map((section) => (
        <View key={section.heading} className="mb-7">
          <Text className="font-semibold text-lg text-ink-900 dark:text-ink-100 mb-2">
            {section.heading}
          </Text>
          <Text className="text-base text-ink-600 dark:text-ink-300 leading-7">
            {section.body}
          </Text>
        </View>
      ))}

      <Button label="Back" variant="secondary" onPress={() => router.back()} />
    </ScrollView>
  );
}
