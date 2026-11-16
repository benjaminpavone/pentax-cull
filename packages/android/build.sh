#!/bin/bash
# Builds the Android app without Gradle: aapt2 + javac + d8 + apksigner (a few seconds, even on an old Mac).
# Needs a JDK 17+ and the Android SDK (platform + build-tools). Defaults: ~/Developer/jdk-21*, ~/Developer/android-sdk.
# Usage: packages/android/build.sh [install]   ("install": also installs and starts it on the phone via adb)
set -euo pipefail
cd "$(dirname "$0")"

JAVA_HOME="${JAVA_HOME:-$(ls -d ~/Developer/jdk-*/Contents/Home 2>/dev/null | sort -V | tail -1)}"
ANDROID_HOME="${ANDROID_HOME:-$HOME/Developer/android-sdk}"
export PATH="$JAVA_HOME/bin:$PATH"
BT=$(ls -d "$ANDROID_HOME"/build-tools/* | sort -V | tail -1)
JAR=$(ls -d "$ANDROID_HOME"/platforms/* | sort -V | tail -1)/android.jar
VERSION_NAME="0.1"
VERSION_CODE=1
OUT=build

rm -rf "$OUT"
mkdir -p "$OUT/classes" "$OUT/dex" "$OUT/gen" "$OUT/assets/viewer"
cp -R ../viewer/. "$OUT/assets/viewer/"

echo "== resources"
"$BT/aapt2" compile --dir res -o "$OUT/res.zip"
"$BT/aapt2" link -I "$JAR" --manifest AndroidManifest.xml -A "$OUT/assets" "$OUT/res.zip" \
  --min-sdk-version 33 --target-sdk-version 36 --version-code $VERSION_CODE --version-name $VERSION_NAME \
  --debug-mode --java "$OUT/gen" -o "$OUT/unsigned.apk"

echo "== java"
javac -nowarn --release 11 -classpath "$JAR" -d "$OUT/classes" $(find src "$OUT/gen" -name '*.java') 2>&1 \
  | grep -v "^warning: \[options\]" || true
[ -n "$(find "$OUT/classes" -name '*.class')" ] || { echo "javac failed"; exit 1; }
"$BT/d8" --min-api 33 --lib "$JAR" --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')
(cd "$OUT/dex" && zip -q ../unsigned.apk classes.dex)

echo "== sign"
KS="$HOME/.android/debug.keystore"
if [ ! -f "$KS" ]; then
  mkdir -p "$HOME/.android"
  keytool -genkeypair -keystore "$KS" -storepass android -keypass android -alias androiddebugkey \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US" >/dev/null
fi
"$BT/zipalign" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/pentax-cull.apk" "$OUT/aligned.apk"
echo "APK: $(pwd)/$OUT/pentax-cull.apk ($(du -h "$OUT/pentax-cull.apk" | cut -f1))"

if [ "${1:-}" = "install" ]; then
  ADB="$ANDROID_HOME/platform-tools/adb"
  "$ADB" install -r "$OUT/pentax-cull.apk"
  "$ADB" shell am start -n local.pentaxcull/.MainActivity
fi
