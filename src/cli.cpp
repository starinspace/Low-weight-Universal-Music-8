#include "internal.hpp"
#include <iostream>
#include <stdexcept>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
int main(int argc, char **argv) {
  try {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    need(argc >= 4,
         "Usage: lum8 encode|decode input output [--preset "
         "best|medium|low|ultra|turbo] [--block 2048] [--optimize]\nUse - for "
         "stdin/stdout. PCM16 WAV input only.");
    std::string mode = argv[1], in = argv[2], out = argv[3], preset = "best";
    int block = 2048;
    bool optimize = false;
    for (int i = 4; i < argc; i++) {
      std::string s = argv[i];
      if (s == "--preset" && i + 1 < argc)
        preset = argv[++i];
      else if (s == "--block" && i + 1 < argc)
        block = std::stoi(argv[++i]);
      else if (s == "--optimize")
        optimize = true;
      else
        throw std::runtime_error("Unknown argument");
    }
    Bytes b = readfile(in);
    need(b.size() <= LIMIT, "Input limit 512 MiB");
    if (mode == "encode") {
      b = encode(wav(b), preset, block, optimize);
      if (out.size() >= 6 && out.substr(out.size() - 6) == ".lum8z")
        b = bestpack(b, false).first;
    } else if (mode == "decode")
      b = wave(decode(std::move(b)));
    else
      throw std::runtime_error("Unknown command");
    writefile(out, b);
    return 0;
  } catch (const std::exception &e) {
    std::cerr << "LUM8: " << e.what() << '\n';
    return 1;
  }
}
