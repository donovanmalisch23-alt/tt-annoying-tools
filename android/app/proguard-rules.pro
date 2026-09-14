# The TeamTalk SDK is a JNI library: every class and field the native layer
# touches by name must survive shrinking. Kept unconditional so enabling
# minification later cannot silently break the SDK.
-keep class dk.bearware.** { *; }
-keepclassmembers class dk.bearware.** { *; }
-dontwarn dk.bearware.**
