from __future__ import division

from math import log, ceil, floor
import os
import re
from subprocess import Popen, PIPE
import sys
from tempfile import TemporaryFile
from warnings import warn
import json

try:
    import audioop
except ImportError:
    import pyaudioop as audioop


if sys.version_info >= (3, 0):
    basestring = str



FRAME_WIDTHS = {
    8: 1,
    16: 2,
    32: 4,
}
ARRAY_TYPES = {
    8:  "b",
    16: "h",
    32: "i",
}
ARRAY_RANGES = {
    8: (-0x80, 0x7f),
    16: (-0x8000, 0x7fff),
    32: (-0x80000000, 0x7fffffff),
}


def get_frame_width(bit_depth):
    return FRAME_WIDTHS[bit_depth]


def get_array_type(bit_depth, signed=True):
    t = ARRAY_TYPES[bit_depth]
    if not signed:
        t = t.upper()
    return t


def get_min_max_value(bit_depth):
    return ARRAY_RANGES[bit_depth]


def _fd_or_path_or_tempfile(fd, mode='w+b', tempfile=True):
    if fd is None and tempfile:
        fd = TemporaryFile(mode=mode)

    if isinstance(fd, basestring):
        fd = open(fd, mode=mode)

    try:
        if isinstance(fd, os.PathLike):
            fd = open(fd, mode=mode)
    except AttributeError:
        # module os has no attribute PathLike, so we're on python < 3.6.
        # The protocol we're trying to support doesn't exist, so just pass.
        pass

    return fd


def db_to_float(db, using_amplitude=True):
    """
    Converts the input db to a float, which represents the equivalent
    ratio in power.
    """
    db = float(db)
    if using_amplitude:
        return 10 ** (db / 20)
    else: # using power
        return 10 ** (db / 10)


def ratio_to_db(ratio, val2=None, using_amplitude=True):
    """
    Converts the input float to db, which represents the equivalent
    to the ratio in power represented by the multiplier passed in.
    """
    ratio = float(ratio)

    # accept 2 values and use the ratio of val1 to val2
    if val2 is not None:
        ratio = ratio / val2
    
    # special case for multiply-by-zero (convert to silence)
    if ratio == 0:
        return -float('inf')

    if using_amplitude:
        return 20 * log(ratio, 10)
    else: # using power
        return 10 * log(ratio, 10)
    

def register_pydub_effect(fn, name=None):
    """
    decorator for adding pydub effects to the AudioSegment objects.

    example use:

        @register_pydub_effect
        def normalize(audio_segment):
            ...

    or you can specify a name:

        @register_pydub_effect("normalize")
        def normalize_audio_segment(audio_segment):
            ...

    """
    if isinstance(fn, basestring):
        name = fn
        return lambda fn: register_pydub_effect(fn, name)

    if name is None:
        name = fn.__name__

    from .audio_segment import AudioSegment
    setattr(AudioSegment, name, fn)
    return fn


def make_chunks(audio_segment, chunk_length):
    """
    Breaks an AudioSegment into chunks that are <chunk_length> milliseconds
    long.

    if chunk_length is 50 then you'll get a list of 50 millisecond long audio
    segments back (except the last one, which can be shorter)
    """
    number_of_chunks = ceil(len(audio_segment) / float(chunk_length))
    return [audio_segment[i * chunk_length:(i + 1) * chunk_length]
            for i in range(int(number_of_chunks))]


def which(program):
    """
    Mimics behavior of UNIX which command.
    """
    #Add .exe program extension for windows support
    if os.name == "nt" and not program.endswith(".exe"):
        program += ".exe"

    envdir_list = [os.curdir] + os.environ["PATH"].split(os.pathsep)

    for envdir in envdir_list:
        program_path = os.path.join(envdir, program)
        if os.path.isfile(program_path) and os.access(program_path, os.X_OK):
            return program_path


def get_encoder_name():
    """
    Return enconder default application for system, either avconv or ffmpeg
    """
    if which("avconv"):
        return "avconv"
    elif which("ffmpeg"):
        return "ffmpeg"
    else:
        # should raise exception
        warn("Couldn't find ffmpeg or avconv - defaulting to ffmpeg, but may not work", RuntimeWarning)
        return "ffmpeg"

def get_player_name():
    """
    Return enconder default application for system, either avconv or ffmpeg
    """
    if which("avplay"):
        return "avplay"
    elif which("ffplay"):
        return "ffplay"
    else:
        # should raise exception
        warn("Couldn't find ffplay or avplay - defaulting to ffplay, but may not work", RuntimeWarning)
        return "ffplay"


def get_prober_name():
    """
    Return probe application, either avconv or ffmpeg
    """
    if which("avprobe"):
        return "avprobe"
    elif which("ffprobe"):
        return "ffprobe"
    else:
        # should raise exception
        warn("Couldn't find ffprobe or avprobe - defaulting to ffprobe, but may not work", RuntimeWarning)
        return "ffprobe"


def mediainfo_json(filepath):
    """Return json dictionary with media info(codec, duration, size, bitrate...) from filepath
    """

    prober = get_prober_name()
    command_args = [
        "-v", "info",
        "-show_format",
        "-show_streams",
        filepath
    ]

    command = [prober, '-of', 'json'] + command_args
    res = Popen(command, stdout=PIPE, stderr=PIPE)
    output, stderr = res.communicate()
    output = output.decode("utf-8", 'ignore')
    stderr = stderr.decode("utf-8", 'ignore')

    info = json.loads(output)

    if not info:
        # If ffprobe didn't give any information, just return it
        # (for example, because the file doesn't exist)
        return info

    # avprobe sometimes gives more information on stderr than
    # on the json output. The information has to be extracted
    # from lines of the format of:
    # '    Stream #0:0: Audio: flac, 88200 Hz, stereo, s32 (24 bit)'
    extra_info = {}
    for line in stderr.split("\n"):
        if '    Stream #0' in line:
            tokens = [x.strip() for x in re.split('[:(),]', line)]
            extra_info[int(tokens[1])] = tokens[2:]

    audio_streams = [x for x in info['streams'] if x['codec_type'] == 'audio']
    if len(audio_streams) == 0:
        return info

    # We just operate on the first audio stream in case there are more
    stream = audio_streams[0]

    def set_property(stream, prop, value):
        if prop not in stream or stream[prop] == 0:
            stream[prop] = value

    for token in extra_info[stream['index']]:
        m = re.match('([su]([0-9]{1,2})p?) \(([0-9]{1,2}) bit\)$', token)
        m2 = re.match('([su]([0-9]{1,2})p?)$', token)
        if m:
            set_property(stream, 'sample_fmt', m.group(1))
            set_property(stream, 'bits_per_sample', int(m.group(2)))
            set_property(stream, 'bits_per_raw_sample', int(m.group(3)))
        elif m2:
            set_property(stream, 'sample_fmt', m2.group(1))
            set_property(stream, 'bits_per_sample', int(m2.group(2)))
            set_property(stream, 'bits_per_raw_sample', int(m2.group(2)))
        elif re.match('fltp?$', token):
            set_property(stream, 'sample_fmt', token)
            set_property(stream, 'bits_per_sample', 32)
            set_property(stream, 'bits_per_raw_sample', 32)
        elif re.match('dlbp?$', token):
            set_property(stream, 'sample_fmt', token)
            set_property(stream, 'bits_per_sample', 64)
            set_property(stream, 'bits_per_raw_sample', 64)

    return info


def mediainfo(filepath):
    """Return dictionary with media info(codec, duration, size, bitrate...) from filepath
    """

    prober = get_prober_name()
    command_args = [
        "-v", "quiet",
        "-show_format",
        "-show_streams",
        filepath
    ]

    command = [prober, '-of', 'old'] + command_args
    res = Popen(command, stdout=PIPE)
    output = res.communicate()[0].decode("utf-8")

    if res.returncode != 0:
        command = [prober] + command_args
        output = Popen(command, stdout=PIPE).communicate()[0].decode("utf-8")

    rgx = re.compile(r"(?:(?P<inner_dict>.*?):)?(?P<key>.*?)\=(?P<value>.*?)$")
    info = {}

    if sys.platform == 'win32':
        output = output.replace("\r", "")

    for line in output.split("\n"):
        # print(line)
        mobj = rgx.match(line)

        if mobj:
            # print(mobj.groups())
            inner_dict, key, value = mobj.groups()

            if inner_dict:
                try:
                    info[inner_dict]
                except KeyError:
                    info[inner_dict] = {}
                info[inner_dict][key] = value
            else:
                info[key] = value

    return info


def check_module_availability(modulename):
    if sys.version_info[0] < 3:
        import imp
        try:
            imp.find_module(modulename)
        except ImportError:
            return False
        return True

    import importlib
    if sys.version_info <= (3, 3):
        loader = importlib.find_loader(modulename)
    else:
        loader = importlib.util.find_spec(modulename)
    return loader is not None
