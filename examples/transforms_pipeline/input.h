// examples/transforms_pipeline/input.h
#ifndef MYAUDIO_H
#define MYAUDIO_H

typedef struct ma_device {
    int id;
    int sample_rate;
    int channels;
} ma_device;

typedef struct ma_context {
    ma_device* default_device;
} ma_context;

void ma_init(ma_context* ctx);
void ma_play(ma_device* dev);
void ma_close(ma_context* ctx);

#endif // MYAUDIO_H
