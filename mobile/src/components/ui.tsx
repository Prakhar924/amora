/**
 * Shared UI primitives.
 *
 * Every screen is built from these so spacing, radii, typography and the
 * pressed/disabled/loading states stay consistent, and so the empty and error
 * states are real components rather than something each screen reinvents.
 */

import React from 'react';
import {
  ActivityIndicator,
  Pressable,
  type PressableProps,
  ScrollView,
  Text,
  TextInput,
  type TextInputProps,
  View,
} from 'react-native';

// --------------------------------------------------------------------------
// Buttons
// --------------------------------------------------------------------------
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

interface ButtonProps extends Omit<PressableProps, 'children'> {
  label: string;
  variant?: ButtonVariant;
  loading?: boolean;
  fullWidth?: boolean;
  icon?: React.ReactNode;
}

const BUTTON_BASE =
  'flex-row items-center justify-center rounded-pill px-6 py-4 gap-2';

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-rose-500 active:bg-rose-600',
  secondary:
    'bg-white dark:bg-ink-800 border border-ink-200 dark:border-ink-700 active:bg-ink-100 dark:active:bg-ink-700',
  ghost: 'bg-transparent active:bg-ink-100 dark:active:bg-ink-800',
  danger: 'bg-danger active:opacity-80',
};

const BUTTON_TEXT: Record<ButtonVariant, string> = {
  primary: 'text-white',
  secondary: 'text-ink-800 dark:text-ink-100',
  ghost: 'text-rose-500',
  danger: 'text-white',
};

export function Button({
  label,
  variant = 'primary',
  loading = false,
  fullWidth = true,
  icon,
  disabled,
  ...rest
}: ButtonProps) {
  const isDisabled = disabled || loading;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: isDisabled, busy: loading }}
      disabled={isDisabled}
      className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${
        fullWidth ? 'w-full' : ''
      } ${isDisabled ? 'opacity-50' : ''}`}
      {...rest}
    >
      {loading ? (
        <ActivityIndicator
          size="small"
          color={variant === 'primary' || variant === 'danger' ? '#fff' : '#E0366B'}
        />
      ) : (
        <>
          {icon}
          <Text className={`font-semibold text-base ${BUTTON_TEXT[variant]}`}>
            {label}
          </Text>
        </>
      )}
    </Pressable>
  );
}

// --------------------------------------------------------------------------
// Text input
// --------------------------------------------------------------------------
interface FieldProps extends TextInputProps {
  label: string;
  error?: string | null;
  hint?: string;
}

export function Field({ label, error, hint, ...rest }: FieldProps) {
  return (
    <View className="w-full mb-4">
      <Text className="text-sm font-medium text-ink-600 dark:text-ink-300 mb-2">
        {label}
      </Text>
      <TextInput
        placeholderTextColor="#9B8C94"
        className={`w-full rounded-2xl px-4 py-4 text-base bg-white dark:bg-ink-800 text-ink-900 dark:text-ink-50 border ${
          error ? 'border-danger' : 'border-ink-200 dark:border-ink-700'
        }`}
        {...rest}
      />
      {hint && !error ? (
        <Text className="text-xs text-ink-400 mt-1.5">{hint}</Text>
      ) : null}
      {error ? (
        <Text className="text-xs text-danger mt-1.5">{error}</Text>
      ) : null}
    </View>
  );
}

// --------------------------------------------------------------------------
// Layout & feedback
// --------------------------------------------------------------------------
export function Screen({
  children,
  scroll = false,
  className = '',
}: {
  children: React.ReactNode;
  scroll?: boolean;
  className?: string;
}) {
  const classes = `flex-1 bg-ink-50 dark:bg-ink-900 ${className}`;
  if (scroll) {
    return (
      <ScrollView
        className={classes}
        contentContainerClassName="px-6 py-6"
        keyboardShouldPersistTaps="handled"
      >
        {children}
      </ScrollView>
    );
  }
  return <View className={classes}>{children}</View>;
}

export function Title({ children }: { children: React.ReactNode }) {
  return (
    <Text className="font-display text-3xl text-ink-900 dark:text-ink-50 mb-2">
      {children}
    </Text>
  );
}

export function Subtitle({ children }: { children: React.ReactNode }) {
  return (
    <Text className="text-base text-ink-500 dark:text-ink-300 leading-6 mb-6">
      {children}
    </Text>
  );
}

export function Card({
  children,
  className = '',
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <View
      className={`bg-white dark:bg-ink-800 rounded-card p-5 border border-ink-100 dark:border-ink-700 ${className}`}
    >
      {children}
    </View>
  );
}

/** Full-screen loading state, used while a screen's first fetch is in flight. */
export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <View className="flex-1 items-center justify-center bg-ink-50 dark:bg-ink-900 gap-3">
      <ActivityIndicator size="large" color="#E0366B" />
      <Text className="text-ink-400 text-sm">{label}</Text>
    </View>
  );
}

/**
 * The empty state. Deliberately a first-class component: a dating app is
 * mostly empty states early on, and "no matches yet" needs to feel like
 * encouragement rather than a failure.
 */
export function Empty({
  emoji,
  title,
  body,
  action,
}: {
  emoji: string;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <View className="flex-1 items-center justify-center px-10 gap-3">
      <Text className="text-5xl mb-2">{emoji}</Text>
      <Text className="font-display text-2xl text-center text-ink-900 dark:text-ink-50">
        {title}
      </Text>
      <Text className="text-center text-ink-500 dark:text-ink-400 leading-6">
        {body}
      </Text>
      {action ? <View className="mt-4 w-full">{action}</View> : null}
    </View>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <View className="flex-1 items-center justify-center px-10 gap-3">
      <Text className="text-4xl mb-1">⚠️</Text>
      <Text className="font-semibold text-lg text-center text-ink-900 dark:text-ink-50">
        Something went wrong
      </Text>
      <Text className="text-center text-ink-500 dark:text-ink-400 leading-6">
        {message}
      </Text>
      {onRetry ? (
        <View className="mt-4 w-full">
          <Button label="Try again" variant="secondary" onPress={onRetry} />
        </View>
      ) : null}
    </View>
  );
}

/** An inline error, used above a form's submit button. */
export function Banner({
  message,
  tone = 'error',
}: {
  message: string;
  tone?: 'error' | 'info' | 'success';
}) {
  const tones = {
    error: 'bg-danger/10 border-danger/30 text-danger',
    info: 'bg-plum-100 border-plum-300 text-plum-700',
    success: 'bg-success/10 border-success/30 text-success',
  } as const;
  return (
    <View className={`rounded-2xl border px-4 py-3 mb-4 ${tones[tone]}`}>
      <Text className={`text-sm ${tones[tone].split(' ').pop()}`}>{message}</Text>
    </View>
  );
}

export function Chip({
  label,
  selected = false,
  onPress,
}: {
  label: string;
  selected?: boolean;
  onPress?: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole={onPress ? 'button' : undefined}
      accessibilityState={{ selected }}
      className={`rounded-pill px-4 py-2 border ${
        selected
          ? 'bg-rose-500 border-rose-500'
          : 'bg-white dark:bg-ink-800 border-ink-200 dark:border-ink-700'
      }`}
    >
      <Text
        className={`text-sm font-medium ${
          selected ? 'text-white' : 'text-ink-600 dark:text-ink-300'
        }`}
      >
        {label}
      </Text>
    </Pressable>
  );
}

export function Divider() {
  return <View className="h-px bg-ink-200 dark:bg-ink-700 my-4" />;
}

/** A labelled row for settings screens. */
export function Row({
  label,
  value,
  onPress,
  danger = false,
}: {
  label: string;
  value?: string;
  onPress?: () => void;
  danger?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress}
      accessibilityRole={onPress ? 'button' : undefined}
      className="flex-row items-center justify-between py-4 active:opacity-60"
    >
      <Text
        className={`text-base ${
          danger ? 'text-danger' : 'text-ink-800 dark:text-ink-100'
        }`}
      >
        {label}
      </Text>
      <View className="flex-row items-center gap-2">
        {value ? (
          <Text className="text-sm text-ink-400">{value}</Text>
        ) : null}
        {onPress ? <Text className="text-ink-300 text-lg">›</Text> : null}
      </View>
    </Pressable>
  );
}
