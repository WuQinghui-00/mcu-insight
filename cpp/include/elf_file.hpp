// fwres —— 嵌入式固件资源分析器（C++17 重写版）
//
// 这个类负责一件事：把一个 ELF 文件读进来，解析出节头表，交给上层统计 Flash/RAM。
// 设计要点：
//   1. 构造即完成读取和校验，失败抛异常（RAII，不返回错误码让调用方到处检查）
//   2. 文件内容存在 std::vector<uint8_t> 里，析构自动释放，不用手写 fclose
//   3. 对外只暴露只读访问，内部布局不泄露
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace fwres {

// 节类型（ELF 规范 sh_type，只列我们用得到的）
enum SectionType : std::uint32_t {
    SHT_NULL     = 0,
    SHT_PROGBITS = 1,
    SHT_SYMTAB   = 2,
    SHT_STRTAB   = 3,
    SHT_NOBITS   = 8,   // 只占内存不占文件，例如 .bss
};

// 节标志（sh_flags）
enum SectionFlags : std::uint32_t {
    SHF_WRITE     = 0x1,  // 运行时可写 -> 落 RAM
    SHF_ALLOC     = 0x2,  // 运行时占内存 -> 才计入 Flash/RAM
    SHF_EXECINSTR = 0x4,  // 可执行指令
};

struct SectionInfo {
    std::string   name;
    std::uint32_t type   = 0;
    std::uint64_t flags  = 0;
    std::uint64_t addr   = 0;
    std::uint64_t offset = 0;   // 在文件里的偏移
    std::uint64_t size   = 0;

    bool is_alloc()    const { return (flags & SHF_ALLOC) != 0; }
    bool is_writable() const { return (flags & SHF_WRITE) != 0; }
    bool is_code()     const { return (flags & SHF_EXECINSTR) != 0; }
    bool is_nobits()   const { return type == SHT_NOBITS; }
};

class ElfFile {
public:
    explicit ElfFile(const std::string& path);

    const std::string& path() const { return path_; }
    std::uint8_t  elf_class() const { return elf_class_; }      // 1=32位 2=64位
    std::uint8_t  data_encoding() const { return data_encoding_; }
    std::uint16_t machine() const { return machine_; }
    std::uint16_t elf_type() const { return elf_type_; }
    std::uint64_t entry() const { return entry_; }
    const std::vector<SectionInfo>& sections() const { return sections_; }

private:
    void parse();

    std::string path_;
    std::vector<std::uint8_t> raw_;     // 整个文件；RAII 由 vector 负责
    std::uint8_t  elf_class_     = 0;
    std::uint8_t  data_encoding_ = 0;
    std::uint16_t machine_       = 0;
    std::uint16_t elf_type_      = 0;
    std::uint64_t entry_         = 0;
    std::vector<SectionInfo> sections_;
};

const char* machine_name(std::uint16_t machine);
const char* section_type_name(std::uint32_t type);

}  // namespace fwres