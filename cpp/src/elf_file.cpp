#include "elf_file.hpp"

#include <cstring>
#include <fstream>
#include <stdexcept>
#include <utility>

namespace fwres {
namespace {

constexpr std::size_t  EI_NIDENT   = 16;
constexpr std::size_t  EI_CLASS    = 4;
constexpr std::size_t  EI_DATA     = 5;
constexpr std::uint8_t ELFCLASS32  = 1;
constexpr std::uint8_t ELFCLASS64  = 2;
constexpr std::uint8_t ELFDATA2LSB = 1;
constexpr std::uint8_t ELFDATA2MSB = 2;
constexpr std::uint16_t SHN_XINDEX = 0xffff;   // 下标溢出时的哨兵值

std::uint16_t bswap16(std::uint16_t v) {
    return static_cast<std::uint16_t>((v >> 8) | (v << 8));
}
std::uint32_t bswap32(std::uint32_t v) {
    return ((v & 0x000000FFu) << 24) | ((v & 0x0000FF00u) << 8) |
           ((v & 0x00FF0000u) >> 8)  | ((v & 0xFF000000u) >> 24);
}
std::uint64_t bswap64(std::uint64_t v) {
    return (static_cast<std::uint64_t>(bswap32(static_cast<std::uint32_t>(v))) << 32) |
           bswap32(static_cast<std::uint32_t>(v >> 32));
}

// 为什么用 memcpy 而不是 (uint32_t *)(p) 直接解引用：
//   1. p 只保证是字节地址，不保证 4 字节对齐。x86 容忍未对齐访问，
//      但 xtensa / ARM 的某些指令会直接抛异常，是未定义行为。
//   2. memcpy 是标准明确允许的"类型双关"写法，编译器会把它优化成一条加载指令，
//      没有额外开销。
std::uint16_t rd16(const std::uint8_t* p, bool le) {
    std::uint16_t v = 0;
    std::memcpy(&v, p, sizeof(v));
    return le ? v : bswap16(v);
}
std::uint32_t rd32(const std::uint8_t* p, bool le) {
    std::uint32_t v = 0;
    std::memcpy(&v, p, sizeof(v));
    return le ? v : bswap32(v);
}
std::uint64_t rd64(const std::uint8_t* p, bool le) {
    std::uint64_t v = 0;
    std::memcpy(&v, p, sizeof(v));
    return le ? v : bswap64(v);
}

void need(std::uint64_t off, std::uint64_t len, std::uint64_t total) {
    if (off > total || len > total - off) {
        throw std::runtime_error("ELF 文件被截断或偏移越界");
    }
}

}  // namespace

ElfFile::ElfFile(const std::string& path) : path_(path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("无法打开文件: " + path);
    }
    in.seekg(0, std::ios::end);
    const std::streamoff end = in.tellg();
    if (end <= 0) {
        throw std::runtime_error("文件为空或无法取得大小: " + path);
    }
    in.seekg(0, std::ios::beg);
    raw_.resize(static_cast<std::size_t>(end));
    in.read(reinterpret_cast<char*>(raw_.data()), static_cast<std::streamsize>(end));
    if (!in) {
        throw std::runtime_error("读取文件内容失败: " + path);
    }
    parse();
}

void ElfFile::parse() {
    const std::uint64_t total = raw_.size();
    need(0, EI_NIDENT, total);
    const std::uint8_t* p = raw_.data();

    // e_ident 前四个字节是魔数 0x7F 'E' 'L' 'F'
    if (p[0] != 0x7f || p[1] != 'E' || p[2] != 'L' || p[3] != 'F') {
        throw std::runtime_error("不是 ELF 文件（魔数不匹配）");
    }
    elf_class_     = p[EI_CLASS];
    data_encoding_ = p[EI_DATA];
    if (elf_class_ != ELFCLASS32 && elf_class_ != ELFCLASS64) {
        throw std::runtime_error("不认识的 ELF 位宽");
    }
    if (data_encoding_ != ELFDATA2LSB && data_encoding_ != ELFDATA2MSB) {
        throw std::runtime_error("不认识的 ELF 字节序");
    }
    const bool le = (data_encoding_ == ELFDATA2LSB);

    // ELF 头字段偏移：32 位和 64 位不一样，所以分开读
    std::uint64_t shoff = 0;
    std::uint64_t entry = 0;
    std::uint16_t shentsize = 0;
    std::uint16_t shnum     = 0;
    std::uint16_t shstrndx  = 0;

    if (elf_class_ == ELFCLASS32) {
        need(16, 36, total);                       // 头总共 52 字节
        elf_type_ = rd16(p + 16, le);
        machine_  = rd16(p + 18, le);
        entry     = rd32(p + 24, le);
        shoff     = rd32(p + 32, le);              // 节头表在文件里的偏移
        shentsize = rd16(p + 46, le);
        shnum     = rd16(p + 48, le);
        shstrndx  = rd16(p + 50, le);
    } else {
        need(16, 48, total);                       // 头总共 64 字节
        elf_type_ = rd16(p + 16, le);
        machine_  = rd16(p + 18, le);
        entry     = rd64(p + 24, le);
        shoff     = rd64(p + 40, le);
        shentsize = rd16(p + 58, le);
        shnum     = rd16(p + 60, le);
        shstrndx  = rd16(p + 62, le);
    }
    entry_ = entry;

    const std::uint16_t min_entsize = (elf_class_ == ELFCLASS32) ? 40 : 64;
    if (shentsize < min_entsize) {
        throw std::runtime_error("节头表条目大小异常");
    }
    if (shoff == 0) {
        return;   // 被 strip 掉节头表的固件
    }
    need(shoff, shentsize, total);

    // 第 0 号节头是"扩展信息"的载体：
    //   节数量超过 0xFF00 时 e_shnum 写 0，真实数量放在 [0].sh_size；
    //   名字表下标超过 0xFF00 时 e_shstrndx 写 0xFFFF，真实下标放在 [0].sh_link。
    // 工具链生成的固件经常有几百个节，这里必须处理，否则会漏读。
    const std::uint8_t* sh0 = p + shoff;
    std::uint64_t count = shnum;
    if (count == 0) {
        count = (elf_class_ == ELFCLASS32) ? rd32(sh0 + 20, le) : rd64(sh0 + 32, le);
    }
    std::uint64_t stridx = shstrndx;
    if (stridx == SHN_XINDEX) {
        stridx = rd32(sh0 + 24, le);               // sh_link 两种位宽都是 32 位
    }

    need(shoff, count * shentsize, total);

    std::vector<SectionInfo> tmp(static_cast<std::size_t>(count));
    std::vector<std::uint32_t> name_off(static_cast<std::size_t>(count), 0);

    for (std::uint64_t i = 0; i < count; ++i) {
        const std::uint8_t* s = p + shoff + i * shentsize;
        name_off[static_cast<std::size_t>(i)] = rd32(s + 0, le);
        SectionInfo& si = tmp[static_cast<std::size_t>(i)];
        si.type = rd32(s + 4, le);
        if (elf_class_ == ELFCLASS32) {
            si.flags  = rd32(s + 8, le);
            si.addr   = rd32(s + 12, le);
            si.offset = rd32(s + 16, le);
            si.size   = rd32(s + 20, le);
        } else {
            si.flags  = rd64(s + 8, le);
            si.addr   = rd64(s + 16, le);
            si.offset = rd64(s + 24, le);
            si.size   = rd64(s + 32, le);
        }
    }

    // 节名字都在 .shstrtab 里，靠 sh_name 这个下标去查
    if (stridx != 0 && stridx < count) {
        const SectionInfo& str = tmp[static_cast<std::size_t>(stridx)];
        if (str.type == SHT_STRTAB) {
            need(str.offset, str.size, total);
            const char* base = reinterpret_cast<const char*>(p + str.offset);
            for (std::uint64_t i = 0; i < count; ++i) {
                const std::uint32_t no = name_off[static_cast<std::size_t>(i)];
                if (no >= str.size) {
                    tmp[static_cast<std::size_t>(i)].name = "<名字越界>";
                    continue;
                }
                const char* beg = base + no;
                const void* end = std::memchr(beg, '\0', static_cast<std::size_t>(str.size - no));
                const std::size_t len = end ? static_cast<std::size_t>(static_cast<const char*>(end) - beg) : 0;
                tmp[static_cast<std::size_t>(i)].name.assign(beg, len);
            }
        }
    }

    sections_ = std::move(tmp);
}

const char* machine_name(std::uint16_t machine) {
    switch (machine) {
        case 3:   return "EM_386 (x86 32位)";
        case 40:  return "EM_ARM";
        case 62:  return "EM_X86_64";
        case 94:  return "EM_XTENSA (ESP32)";
        case 183: return "EM_AARCH64";
        case 243: return "EM_RISCV";
        default:  return "未知架构";
    }
}

const char* section_type_name(std::uint32_t type) {
    switch (type) {
        case 0:  return "NULL";
        case 1:  return "PROGBITS";
        case 2:  return "SYMTAB";
        case 3:  return "STRTAB";
        case 4:  return "RELA";
        case 8:  return "NOBITS";
        case 9:  return "REL";
        case 14: return "INIT_ARRAY";
        case 15: return "FINI_ARRAY";
        case 17: return "GROUP";
        default: return "OTHER";
    }
}

}  // namespace fwres