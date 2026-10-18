// fwres 单元测试
//
// 原则：优先测"纯函数"（给输入、验输出），文件解析用临时文件构造最小样例。
// 这样测试不依赖真实固件，CI 上也能跑。
#include <gtest/gtest.h>

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

#include "map_file.hpp"
#include "reporter.hpp"
#include "telemetry.hpp"

namespace {

void write_file(const std::string& path, const std::string& content) {
    std::ofstream out(path, std::ios::binary);
    out << content;
}

}  // namespace

// ---------- 地址分区 ----------
TEST(Region, Esp32MemoryMap) {
    EXPECT_EQ(fwres::region_of(0x40080000ULL), fwres::Region::Iram);
    EXPECT_EQ(fwres::region_of(0x40080404ULL), fwres::Region::Iram);
    EXPECT_EQ(fwres::region_of(0x400d0020ULL), fwres::Region::FlashIrom);
    EXPECT_EQ(fwres::region_of(0x3f400120ULL), fwres::Region::FlashDrom);
    EXPECT_EQ(fwres::region_of(0x3ffb0000ULL), fwres::Region::Dram);
    EXPECT_EQ(fwres::region_of(0x50001fe8ULL), fwres::Region::Rtc);
    EXPECT_EQ(fwres::region_of(0x00000000ULL), fwres::Region::Other);
}

TEST(Region, BoundariesAreExclusive) {
    EXPECT_EQ(fwres::region_of(0x400A0000ULL), fwres::Region::Other);
    EXPECT_EQ(fwres::region_of(0x40400000ULL), fwres::Region::Other);
    EXPECT_EQ(fwres::region_of(0x3F800000ULL), fwres::Region::Other);
}

// ---------- 格式化工具 ----------
TEST(Format, Commas) {
    EXPECT_EQ(fwres::commas(0), "0");
    EXPECT_EQ(fwres::commas(999), "999");
    EXPECT_EQ(fwres::commas(1000), "1,000");
    EXPECT_EQ(fwres::commas(1234567), "1,234,567");
}

TEST(Format, HumanReadable) {
    EXPECT_EQ(fwres::human(512), "512.00 B");
    EXPECT_EQ(fwres::human(1024), "1.00 KB");
    EXPECT_EQ(fwres::human(1024 * 1024), "1.00 MB");
}

TEST(Format, Utf8Padding) {
    EXPECT_EQ(fwres::pad_display("abc", 5), "abc  ");
    EXPECT_EQ(fwres::pad_display("中文", 6), "中文  ");
}

// ---------- .map 解析 ----------
TEST(MapFile, ParsesOneLineAndTwoLineForms) {
    const std::string path = "test_map.tmp";
    write_file(path,
        "Memory Configuration\n"
        "Name  Origin  Length\n"
        "\n"
        "Linker script and memory map\n"
        "\n"
        ".flash.text     0x400d0020    0x20\n"
        " .literal.foo\n"
        "                0x400d0020        0x8 libfoo.a(a.o)\n"
        " .text          0x400d0028       0x18 libfoo.a(a.o)\n");

    fwres::MapFile mf(path);
    ASSERT_EQ(mf.entries().size(), 2u);

    EXPECT_EQ(mf.entries()[0].section, ".literal.foo");
    EXPECT_EQ(mf.entries()[0].object, "libfoo.a(a.o)");
    EXPECT_EQ(mf.entries()[0].addr, 0x400d0020u);
    EXPECT_EQ(mf.entries()[0].size, 0x8u);

    EXPECT_EQ(mf.entries()[1].section, ".text");
    EXPECT_EQ(mf.entries()[1].size, 0x18u);

    std::remove(path.c_str());
}

TEST(MapFile, IgnoresWildcardsAndRelaxRecords) {
    const std::string path = "test_map2.tmp";
    write_file(path,
        "Linker script and memory map\n"
        ".text  0x400d0020  0x40\n"
        " *(EXCLUDE_FILE(*libfoo.a) .text)\n"
        " .text  0x400d0020  0x10 libfoo.a(a.o)\n"
        "                0x4 (size before relaxing)\n"
        " *fill*         0x400d0030  0x2\n");

    fwres::MapFile mf(path);
    ASSERT_EQ(mf.entries().size(), 1u);
    EXPECT_EQ(mf.entries()[0].size, 0x10u);
    std::remove(path.c_str());
}

// ---------- 遥测 CSV ----------
TEST(Telemetry, ReadsMinMax) {
    const std::string path = "test_tel.tmp";
    write_file(path,
        "metric,min,max\n"
        "heap.min,185568,283908\n"
        "task.format.stack_free_min,56,56\n");

    fwres::Telemetry t(path);
    EXPECT_TRUE(t.has("heap.min"));
    EXPECT_DOUBLE_EQ(t.min_of("heap.min"), 185568.0);
    EXPECT_DOUBLE_EQ(t.max_of("heap.min"), 283908.0);
    EXPECT_TRUE(t.has("task.format.stack_free_min"));
    EXPECT_FALSE(t.has("no.such.metric"));

    fwres::ReportData d;
    std::ostringstream out;
    EXPECT_FALSE(fwres::write_runtime_section(out, d, t));
    EXPECT_NE(out.str().find("format"), std::string::npos);
    std::remove(path.c_str());
}

TEST(Telemetry, PassesWhenAllAboveThreshold) {
    const std::string path = "test_tel2.tmp";
    write_file(path,
        "metric,min,max\n"
        "heap.min,120000,150000\n"
        "task.sample.stack_free_min,2048,2048\n");

    fwres::Telemetry t(path);
    fwres::ReportData d;
    std::ostringstream out;
    EXPECT_TRUE(fwres::write_runtime_section(out, d, t));
    std::remove(path.c_str());
}