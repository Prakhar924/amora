/**
 * Metro configuration, wrapped by NativeWind so Tailwind classes are compiled
 * into styles at bundle time.
 */
const { getDefaultConfig } = require('expo/metro-config');
const { withNativeWind } = require('nativewind/metro');

const config = getDefaultConfig(__dirname);

module.exports = withNativeWind(config, { input: './global.css' });
