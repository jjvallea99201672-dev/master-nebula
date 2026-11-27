// Host test for policy_core.h against vectors produced by the real server code.
// Copyright (c) 2026 Jay Vallea. All Rights Reserved.
#include <cmath>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "../policy_core.h"

static std::vector<std::string> split(const std::string &s, char d) {
  std::vector<std::string> out;
  std::stringstream ss(s);
  std::string item;
  while (std::getline(ss, item, d)) out.push_back(item);
  if (!s.empty() && s.back() == d) out.push_back("");
  return out;
}

static std::string b64(const std::string &in) {
  std::vector<uint8_t> buf(in.size());
  int n = lkpolicy::b64decode(in.c_str(), buf.data(), buf.size());
  return n < 0 ? std::string() : std::string((char *)buf.data(), n);
}

int main(int argc, char **argv) {
  std::ifstream f(argc > 1 ? argv[1] : "vectors.txt");
  std::string line;
  int pass = 0, fail = 0;
  while (std::getline(f, line)) {
    auto c = split(line, '|');
    if (c.size() != 8) { std::cerr << "bad vector line\n"; return 2; }
    // Same path as the firmware: parse the device view, take the un-escaped canonical string.
    const std::string view = b64(c[2]);
    JsonDocument d;
    if (deserializeJson(d, view)) { std::cerr << "bad view json\n"; return 2; }
    JsonString canon = d["canonical"].as<JsonString>();
    const std::string pub = b64(c[4]);
    lkpolicy::Values v = {};
    auto r = lkpolicy::verifyPolicy(canon.c_str(), canon.size(), c[3].c_str(), (const uint8_t *)pub.data(),
                                    nullptr, c[6].c_str(), (uint32_t)std::stoul(c[5]), v);
    bool ok = std::string(lkpolicy::resultName(r)) == c[1];
    if (ok && r == lkpolicy::OK) {
      const float want = c[7] == "nan" ? NAN : std::stof(c[7]);
      ok = (std::isnan(want) && std::isnan(v.beta)) || std::fabs(want - v.beta) < 1e-6f;
      ok = ok && v.version == 2 && strlen(v.sha256) == 64;
    }
    std::cout << (ok ? "PASS " : "FAIL ") << c[0] << " -> " << lkpolicy::resultName(r) << "\n";
    (ok ? pass : fail)++;
  }
  std::cout << pass << " passed, " << fail << " failed\n";
  return fail ? 1 : (pass ? 0 : 3);
}
