/**
 * Tab navigation.
 *
 * The badge on the Chats tab is driven by the live socket as well as by a
 * fetch on focus, so an arriving message updates the count without the user
 * navigating anywhere.
 */

import { useCallback, useEffect, useState } from 'react';
import { Text } from 'react-native';
import { Tabs } from 'expo-router';

import { chat } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { useSocketEvent } from '@/lib/socket';

function TabIcon({ emoji, focused }: { emoji: string; focused: boolean }) {
  return (
    <Text style={{ fontSize: 22, opacity: focused ? 1 : 0.45 }}>{emoji}</Text>
  );
}

export default function TabsLayout() {
  const { isAuthenticated, user } = useAuth();
  const [unread, setUnread] = useState(0);

  const loadUnread = useCallback(async () => {
    if (!isAuthenticated) return;
    try {
      const { total_unread } = await chat.unreadCount();
      setUnread(total_unread);
    } catch {
      // A failed badge refresh is not worth surfacing to the user.
    }
  }, [isAuthenticated]);

  useEffect(() => {
    void loadUnread();
  }, [loadUnread]);

  // Keep the badge honest in real time.
  useSocketEvent((frame) => {
    if (frame.type === 'message.created') {
      // Only the recipient's badge should move.
      if (frame.message.sender_id !== user?.id) {
        setUnread((count) => count + 1);
      }
    } else if (frame.type === 'message.read') {
      if (frame.by_user_id === user?.id) void loadUnread();
    }
  });

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: '#E0366B',
        tabBarInactiveTintColor: '#9B8C94',
        tabBarStyle: {
          borderTopWidth: 0,
          elevation: 12,
          height: 64,
          paddingBottom: 8,
          paddingTop: 8,
        },
        tabBarLabelStyle: { fontSize: 11, fontWeight: '600' },
      }}
    >
      <Tabs.Screen
        name="discover"
        options={{
          title: 'Discover',
          tabBarIcon: ({ focused }) => <TabIcon emoji="🔥" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="matches"
        options={{
          title: 'Matches',
          tabBarIcon: ({ focused }) => <TabIcon emoji="💞" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="chats"
        options={{
          title: 'Chats',
          tabBarIcon: ({ focused }) => <TabIcon emoji="💬" focused={focused} />,
          tabBarBadge: unread > 0 ? (unread > 99 ? '99+' : unread) : undefined,
          tabBarBadgeStyle: { backgroundColor: '#E0366B', fontSize: 10 },
        }}
      />
      <Tabs.Screen
        name="me"
        options={{
          title: 'Me',
          tabBarIcon: ({ focused }) => <TabIcon emoji="🪞" focused={focused} />,
        }}
      />
    </Tabs>
  );
}
