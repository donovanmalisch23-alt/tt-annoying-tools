# TeamTalk SDK goes here

The TeamTalk 5 Android SDK is **not** redistributed in this repository
because its license does not permit it. Download it from BearWare.dk
(<https://bearware.dk/?page_id=419>) and drop the Java bindings jar here:

```
android/app/libs/TeamTalk5.jar
```

and the native libraries into the per-ABI folders:

```
android/app/src/main/jniLibs/arm64-v8a/libTeamTalk5-jni.so
android/app/src/main/jniLibs/armeabi-v7a/libTeamTalk5-jni.so
android/app/src/main/jniLibs/x86_64/libTeamTalk5-jni.so
```

`libs/*.jar` and `src/main/jniLibs/**` are the only paths the build wires up.
See `android/README.md` for the full walkthrough.
