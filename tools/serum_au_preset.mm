#import <AudioToolbox/AudioToolbox.h>
#import <CoreFoundation/CoreFoundation.h>
#import <Foundation/Foundation.h>

#include <cstdio>
#include <cstring>

namespace {
constexpr UInt32 supportedVersion = 0x00020105;
constexpr NSUInteger stateLimit = 16 * 1024 * 1024;
constexpr NSUInteger metadataLimit = 64 * 1024;
constexpr char stateMagic[] = "XferJson";

bool isProcessorState(NSData *data, bool effect) {
    constexpr NSUInteger headerSize = sizeof(stateMagic) + sizeof(uint64_t);
    if (data.length <= headerSize || data.length > stateLimit
        || std::memcmp(data.bytes, stateMagic, sizeof(stateMagic)))
        return false;

    uint64_t length;
    std::memcpy(&length, static_cast<const char *>(data.bytes) + sizeof(stateMagic), sizeof(length));
    length = CFSwapInt64LittleToHost(length);
    if (!length || length > metadataLimit || length >= data.length - headerSize)
        return false;

    NSData *header = [data subdataWithRange:NSMakeRange(headerSize, length)];
    id metadata = [NSJSONSerialization JSONObjectWithData:header options:0 error:nil];
    if (![metadata isKindOfClass:[NSDictionary class]]) return false;
    NSString *product = effect ? @"Serum2FX" : @"Serum2";
    return [metadata[@"component"] isEqual:@"processor"] && [metadata[@"product"] isEqual:product]
        && [metadata[@"productVersion"] isEqual:@"2.1.5"] && [metadata[@"version"] isEqual:@11];
}

NSDictionary *readClassInfo(AudioUnit unit) {
    CFPropertyListRef state = nullptr;
    UInt32 size = sizeof(state);
    OSStatus status = AudioUnitGetProperty(unit, kAudioUnitProperty_ClassInfo,
        kAudioUnitScope_Global, 0, &state, &size);
    if (status != noErr || !state) {
        std::fprintf(stderr, "Could not read Serum AU ClassInfo (%d).\n", status);
        if (state) CFRelease(state);
        return nil;
    }
    id value = CFBridgingRelease(state);
    if (![value isKindOfClass:[NSDictionary class]]) {
        std::fputs("Serum AU ClassInfo is not a dictionary.\n", stderr);
        return nil;
    }
    return value;
}

bool writePreset(AudioUnit unit, bool effect, NSData *processorState, NSString *name, NSString *destination) {
    NSMutableDictionary *initial = [readClassInfo(unit) mutableCopy];
    if (!initial) return false;
    initial[@"Processor State"] = processorState;
    CFPropertyListRef input = (__bridge CFPropertyListRef)initial;
    OSStatus status = AudioUnitSetProperty(unit, kAudioUnitProperty_ClassInfo,
        kAudioUnitScope_Global, 0, &input, sizeof(input));
    if (status != noErr) {
        std::fprintf(stderr, "Serum AU rejected the processor state (%d).\n", status);
        return false;
    }

    AUPreset present{-1, (__bridge CFStringRef)name};
    status = AudioUnitSetProperty(unit, kAudioUnitProperty_PresentPreset,
        kAudioUnitScope_Global, 0, &present, sizeof(present));
    if (status != noErr) {
        std::fprintf(stderr, "Serum AU rejected the preset name (%d).\n", status);
        return false;
    }

    NSDictionary *result = readClassInfo(unit);
    id renderedState = result[@"Processor State"];
    if (![result[@"name"] isEqual:name] || ![renderedState isKindOfClass:[NSData class]]
        || !isProcessorState(renderedState, effect)) {
        std::fputs("Serum AU returned an unexpected name or processor state.\n", stderr);
        return false;
    }

    NSError *error = nil;
    NSData *bytes = [NSPropertyListSerialization dataWithPropertyList:result
        format:NSPropertyListXMLFormat_v1_0 options:0 error:&error];
    if (!bytes || ![bytes writeToFile:destination options:NSDataWritingAtomic error:&error]) {
        const char *reason = error.localizedDescription.UTF8String;
        std::fprintf(stderr, "Could not write Serum AU preset. %s\n", reason ? reason : "");
        return false;
    }
    return true;
}
} // namespace

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 5 || (std::strcmp(argv[1], "instrument") && std::strcmp(argv[1], "fx"))) {
            std::fputs("usage: serum-au-preset instrument|fx INPUT_STATE OUTPUT_AUPRESET NAME\n", stderr);
            return 1;
        }
        bool effect = !std::strcmp(argv[1], "fx");
        NSString *source = [NSString stringWithUTF8String:argv[2]];
        NSString *destination = [NSString stringWithUTF8String:argv[3]];
        NSString *name = [NSString stringWithUTF8String:argv[4]];
        if (!source.length || !destination.length || !name.length) {
            std::fputs("Use UTF-8 paths and a nonempty preset name.\n", stderr);
            return 1;
        }

        NSData *state = [NSData dataWithContentsOfFile:source options:NSDataReadingMappedIfSafe error:nil];
        if (!isProcessorState(state, effect)) {
            std::fputs("Expected the matching Serum 2.1.5 processor state (schema 11).\n", stderr);
            return 2;
        }

        AudioComponentDescription identity{};
        identity.componentType = effect ? kAudioUnitType_MusicEffect : kAudioUnitType_MusicDevice;
        identity.componentSubType = effect ? 'Xf2Y' : 'Xf2X';
        identity.componentManufacturer = 'XFER';
        AudioComponent component = AudioComponentFindNext(nullptr, &identity);
        UInt32 version = 0;
        if (!component || AudioComponentGetVersion(component, &version) != noErr || version != supportedVersion) {
            std::fputs("Serum Audio Unit 2.1.5 was not found.\n", stderr);
            return 3;
        }
        AudioUnit unit = nullptr;
        OSStatus status = AudioComponentInstanceNew(component, &unit);
        if (status != noErr) {
            std::fprintf(stderr, "Could not open Serum Audio Unit (%d).\n", status);
            return 4;
        }

        bool written = writePreset(unit, effect, state, name, destination);
        AudioComponentInstanceDispose(unit);
        return written ? 0 : 5;
    }
}
