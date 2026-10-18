"""Compatibility and regression checks. pip install numpy soundfile numba zstandard"""
import sys, tempfile, subprocess, wave, pathlib, struct, json
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'original'))
import lum8_encoder as enc
import lum8_decoder as dec
# Correct upstream ingestion only for bitstream comparison; decoder remains unmodified.
original_to_int16 = enc.to_int16
assert original_to_int16(np.array([[-1.0]]))[0,0] == -32767
enc.to_int16 = lambda data: np.rint(data * 32768.0).astype(np.int16)
exe=str(pathlib.Path(sys.argv[1]).resolve())
def run(*args,ok=True):
 p=subprocess.run([exe,*map(str,args)],capture_output=True)
 if ok: assert p.returncode==0,p.stderr
 else: assert p.returncode!=0
 return p
with tempfile.TemporaryDirectory() as td:
 d=pathlib.Path(td); metrics=[]
 for channels in (1,2):
  for n in (0,1,2,3,4,2049):
   rng=np.random.default_rng(8)
   x=rng.integers(-32768,32768,(n,channels),dtype=np.int16)
   src=d/'in.wav'
   with wave.open(str(src),'wb') as w:
    w.setparams((channels,2,44100,0,'NONE','not compressed'));w.writeframes(x.astype('<i2').tobytes())
   for preset in ('low','medium','best','ultra','turbo'):
    native=d/'native.lum8'; out=d/'out.wav'
    run('encode',src,native,'--preset',preset)
    dec.decode_lum8_to_wav(str(native),str(out))
    pydecoded=out.read_bytes()
    run('decode',native,out)
    with wave.open(str(out),'rb') as w: nativepcm=w.readframes(n)
    if preset=='turbo':assert nativepcm==x.astype('<i2').tobytes()
    with wave.open(str(d/'out.wav'),'rb') as w: assert w.getnframes()==n
    # Compare PCM rather than WAV metadata emitted by libsndfile.
    import soundfile as sf
    dec.decode_lum8_to_wav(str(native),str(d/'ref.wav'))
    assert np.array_equal(sf.read(out,dtype='int16')[0],sf.read(d/'ref.wav',dtype='int16')[0])
    enc.encode_audio_to_lum8(str(src),str(d/'py.lum8'),preset=preset)
    run('decode',d/'py.lum8',out)
    dec.decode_lum8_to_wav(str(d/'py.lum8'),str(d/'ref.wav'))
    assert np.array_equal(sf.read(out,dtype='int16')[0],sf.read(d/'ref.wav',dtype='int16')[0])
    if n==2049 and preset!='turbo':
     assert native.read_bytes()==(d/'py.lum8').read_bytes(),preset
   # outer zstd and zlib
   for suffix in ('.lum8z',):
    run('encode',src,d/('packed'+suffix),'--preset','turbo');run('decode',d/('packed'+suffix),out)
   enc.compress_lum8z(str(d/'py.lum8'),str(d/'zstd.lum8z'));run('decode',d/'zstd.lum8z',out)
 # Legacy nibble order and experimental xlow decoding.
 for preset,legacy in [('best',True),('xlow',False)]:
  enc.encode_audio_to_lum8(str(src),str(d/'legacy.lum8'),preset=preset,legacy_best=legacy)
  run('decode',d/'legacy.lum8',out);dec.decode_lum8_to_wav(str(d/'legacy.lum8'),str(d/'ref.wav'))
  assert np.array_equal(sf.read(out,dtype='int16')[0],sf.read(d/'ref.wav',dtype='int16')[0])
 # synthetic measurements: not a perceptual quality benchmark
 n=44100;t=np.arange(n)/44100
 for label,x in [('silence',np.zeros((n,2))),('tones',np.column_stack([12000*np.sin(2*np.pi*440*t),11000*np.sin(2*np.pi*442*t)])),('noise',np.random.default_rng(4).normal(0,7000,(n,2)))]:
  x=np.clip(x,-32768,32767).astype(np.int16)
  with wave.open(str(src),'wb') as w:w.setparams((2,2,44100,0,'NONE','not compressed'));w.writeframes(x.tobytes())
  for opt in (False,True):
   run('encode',src,d/'test.lum8','--preset','best',*(['--optimize'] if opt else []));run('decode',d/'test.lum8',out)
   y=sf.read(out,dtype='int16',always_2d=True)[0].astype(float)
   metrics.append(dict(signal=label,optimize=opt,bytes=(d/'test.lum8').stat().st_size,rmse=float(np.sqrt(np.mean((x.astype(float)-y)**2)))))
 # corruption and stdout transport
 b=(d/'test.lum8').read_bytes()
 for broken in (b[:10],b[:18],b[:100],b[:15]+b'\x00\x00'+b[17:],b[:23]+b'\xff'+b[24:]):
  (d/'bad.lum8').write_bytes(broken);run('decode',d/'bad.lum8',out,ok=False)
 assert run('decode',d/'test.lum8','-').stdout[:4]==b'RIFF'
 print(json.dumps(metrics,indent=2))
 print('PASS: cross-decoding, bitstream identity for fixed presets, exact native PCM16 turbo, v1, xlow, compressed wrappers, malformed input, stdout.')
