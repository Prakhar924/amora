/**
 * Report review queue.
 *
 * Each row shows how many reports the same account has accumulated, which is
 * the signal that separates a one-off complaint from a pattern. A moderator
 * can dismiss, action, or action-and-suspend in one step.
 */

import { useCallback, useEffect, useState } from 'react';
import {
  FlatList,
  Modal,
  Pressable,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, admin } from '@/lib/api';
import {
  Banner,
  Button,
  Card,
  Chip,
  Empty,
  ErrorState,
  Loading,
} from '@/components/ui';
import type { AdminReport } from '@/lib/types';

const SUSPENSION_OPTIONS = [0, 1, 7, 30, 365];

export default function ReportQueue() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [reports, setReports] = useState<AdminReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [active, setActive] = useState<AdminReport | null>(null);
  const [resolution, setResolution] = useState('');
  const [suspendDays, setSuspendDays] = useState(0);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setReports(await admin.reports());
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not load reports.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function resolve(status: 'actioned' | 'dismissed') {
    if (!active) return;
    setBusy(true);
    try {
      await admin.resolveReport(active.id, {
        status,
        resolution_note: resolution.trim() || undefined,
        suspend_days:
          status === 'actioned' && suspendDays > 0 ? suspendDays : undefined,
      });
      setReports((current) => current.filter((r) => r.id !== active.id));
      setNotice(
        status === 'actioned'
          ? suspendDays > 0
            ? `Report upheld and the account suspended for ${suspendDays} day(s).`
            : 'Report upheld.'
          : 'Report dismissed.',
      );
      setActive(null);
      setResolution('');
      setSuspendDays(0);
    } catch (caught) {
      setNotice(
        caught instanceof ApiError
          ? caught.message
          : 'Could not resolve that report.',
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Loading label="Loading reports…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} />;

  return (
    <>
      <View
        className="flex-1 bg-ink-50 dark:bg-ink-900"
        style={{ paddingTop: insets.top + 16 }}
      >
        <View className="flex-row items-center gap-2 px-6 mb-3">
          <Pressable
            onPress={() => router.back()}
            accessibilityLabel="Back"
            className="w-9 h-9 items-center justify-center -ml-2"
          >
            <Text className="text-2xl text-ink-600 dark:text-ink-300">‹</Text>
          </Pressable>
          <View className="flex-1">
            <Text className="font-display text-2xl text-ink-900 dark:text-ink-50">
              Reports
            </Text>
            <Text className="text-xs text-ink-400">
              {reports.length} open · oldest first
            </Text>
          </View>
        </View>

        {notice ? (
          <View className="px-6">
            <Banner message={notice} tone="info" />
          </View>
        ) : null}

        {reports.length === 0 ? (
          <Empty
            emoji="🕊️"
            title="No open reports"
            body="Nothing is waiting for review. New reports appear here as soon as they are filed."
          />
        ) : (
          <FlatList
            data={reports}
            keyExtractor={(item) => item.id}
            contentContainerClassName="px-6 pb-10 gap-3"
            renderItem={({ item }) => (
              <Pressable onPress={() => setActive(item)} className="active:opacity-70">
                <Card>
                  <View className="flex-row items-start justify-between gap-3 mb-2">
                    <View className="flex-1">
                      <Text className="font-semibold text-ink-900 dark:text-ink-50">
                        {item.reported_display_name ?? 'Unknown user'}
                      </Text>
                      <Text className="text-xs text-ink-400 mt-0.5">
                        {item.reported_email}
                      </Text>
                    </View>
                    {/* The repeat-offender signal. */}
                    {item.report_count_against_reported > 1 ? (
                      <View className="rounded-pill bg-danger/15 px-2.5 py-1">
                        <Text className="text-danger text-xs font-bold">
                          {item.report_count_against_reported} reports
                        </Text>
                      </View>
                    ) : null}
                  </View>

                  <View className="flex-row flex-wrap gap-2 mb-2">
                    <Chip label={item.reason.replace(/_/g, ' ')} selected />
                  </View>

                  {item.details ? (
                    <Text
                      className="text-sm text-ink-600 dark:text-ink-300 leading-6"
                      numberOfLines={3}
                    >
                      {item.details}
                    </Text>
                  ) : null}

                  {item.message_body ? (
                    <View className="mt-2 rounded-2xl bg-ink-100 dark:bg-ink-900 p-3">
                      <Text className="text-xs text-ink-400 mb-1">
                        Reported message
                      </Text>
                      <Text
                        className="text-sm text-ink-700 dark:text-ink-200"
                        numberOfLines={4}
                      >
                        {item.message_body}
                      </Text>
                    </View>
                  ) : null}

                  <Text className="text-xs text-ink-400 mt-3">
                    Filed {new Date(item.created_at).toLocaleString()}
                  </Text>
                </Card>
              </Pressable>
            )}
          />
        )}
      </View>

      <Modal
        visible={active !== null}
        transparent
        animationType="slide"
        onRequestClose={() => setActive(null)}
      >
        <View className="flex-1 justify-end bg-black/50">
          <View
            className="bg-ink-50 dark:bg-ink-900 rounded-t-card px-6 pt-6"
            style={{ paddingBottom: insets.bottom + 20 }}
          >
            <Text className="font-display text-2xl text-ink-900 dark:text-ink-50 mb-1">
              Resolve report
            </Text>
            <Text className="text-sm text-ink-400 mb-5">
              {active?.reported_display_name ?? 'This account'} ·{' '}
              {active?.reason.replace(/_/g, ' ')}
            </Text>

            <Text className="text-sm font-medium text-ink-600 dark:text-ink-300 mb-2">
              Suspend the account for
            </Text>
            <View className="flex-row flex-wrap gap-2 mb-4">
              {SUSPENSION_OPTIONS.map((days) => (
                <Chip
                  key={days}
                  label={days === 0 ? 'No suspension' : `${days} day${days > 1 ? 's' : ''}`}
                  selected={suspendDays === days}
                  onPress={() => setSuspendDays(days)}
                />
              ))}
            </View>

            <TextInput
              value={resolution}
              onChangeText={setResolution}
              placeholder="Internal note on the decision (optional)"
              placeholderTextColor="#9B8C94"
              multiline
              maxLength={1000}
              className="rounded-2xl px-4 py-3 bg-white dark:bg-ink-800 border border-ink-200 dark:border-ink-700 text-ink-900 dark:text-ink-50 mb-4"
              style={{ minHeight: 80, textAlignVertical: 'top' }}
            />

            <View className="gap-2">
              <Button
                label="Uphold report"
                variant="danger"
                onPress={() => void resolve('actioned')}
                loading={busy}
                disabled={busy}
              />
              <Button
                label="Dismiss report"
                variant="secondary"
                onPress={() => void resolve('dismissed')}
                disabled={busy}
              />
              <Pressable onPress={() => setActive(null)} className="py-3">
                <Text className="text-center text-ink-500">Cancel</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </>
  );
}
