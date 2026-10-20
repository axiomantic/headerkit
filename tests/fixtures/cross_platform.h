#ifndef CROSS_PLATFORM_H
#define CROSS_PLATFORM_H

#define MAKE_FLAG(bit) (1 << (bit))
#define CALC_OFFSET(base, idx) ((base) + (idx) * 4)
#define SAFE_FREE(p) do { if (p) { free(p); (p) = 0; } } while(0)
#define CONCAT_SYM(a, b) a ## b

typedef int Status;

#if defined(__APPLE__)
typedef struct {
    void *audio_queue;
    int sample_rate;
} PlatformAudio;

int mac_audio_start(PlatformAudio *audio);

#elif defined(__linux__)
typedef struct {
    void *alsa_handle;
    int buffer_size;
} PlatformAudio;

int linux_audio_start(PlatformAudio *audio);

#elif defined(_WIN32)
typedef struct {
    void *wasapi_client;
    unsigned int channels;
} PlatformAudio;

int win_audio_start(PlatformAudio *audio);

#endif

typedef struct {
    Status status;
    PlatformAudio audio;
} AppContext;

Status app_init(AppContext *ctx);
void app_shutdown(AppContext *ctx);

#endif /* CROSS_PLATFORM_H */
