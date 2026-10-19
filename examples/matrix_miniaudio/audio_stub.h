// examples/matrix_miniaudio/audio_stub.h
#ifndef AUDIO_STUB_H
#define AUDIO_STUB_H

#define AUDIO_VERSION_MAJOR 2
#define AUDIO_VERSION_MINOR 1
#define AUDIO_VERSION_PATCH 0

#define ENABLE_VORBIS 1
#define ENABLE_FLAC 1

void audio_init(void);

#if defined(_WIN32)
void audio_play_wasapi(int device_id);
#elif defined(__APPLE__)
void audio_play_coreaudio(int device_id);
#else
void audio_play_alsa(int device_id);
#endif

void audio_shutdown(void);

#endif // AUDIO_STUB_H
