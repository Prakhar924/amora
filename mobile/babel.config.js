/**
 * Babel configuration.
 *
 * `jsxImportSource: nativewind` is what lets NativeWind compile `className`
 * on React Native components. The Reanimated plugin must stay last — it
 * rewrites worklets and expects to see the final AST.
 */
module.exports = function (api) {
  api.cache(true);
  return {
    presets: [
      ['babel-preset-expo', { jsxImportSource: 'nativewind' }],
      'nativewind/babel',
    ],
    plugins: ['react-native-worklets/plugin'],
  };
};
