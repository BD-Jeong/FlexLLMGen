#!/usr/bin/env python3

import sys
import os
import time

# 현재 디렉토리를 Python 경로에 추가
sys.path.insert(0, '.')

from nvme_binding import XNVMeNamespace

def test_small_block():
    """max_blocks보다 작은 블록으로 테스트"""
    print("=" * 50)
    print("🔍 SMALL BLOCK 테스트 (max_blocks보다 작음)")
    print("=" * 50)
    
    device_path = "/dev/nvme0n1"
    
    try:
        # 디바이스 초기화
        print("📖 디바이스 초기화...")
        nvme = XNVMeNamespace(device_path, use_io_uring=True)
        print(f"✅ 디바이스 초기화 성공")
        print(f"📊 LBA 크기: {nvme.lba_nbytes} 바이트")
        print(f"📊 max_blocks: {nvme.max_blocks}")
        
        # Small block 테스트 (1 블록 = 512바이트)
        lba_size = nvme.lba_nbytes
        # 정확히 1블록(512B)로 맞춤
        small_data = (b"SMALL_BLOCK_TEST_DATA" * 100)[:lba_size]
        assert len(small_data) == lba_size
        
        print(f"📝 Small block 쓰기 테스트...")
        print(f"   - 데이터 크기: {len(small_data)} 바이트")
        print(f"   - 블록 수: {len(small_data) // lba_size}")
        
        start_time = time.time()
        nvme.write(small_data, lba=100)
        write_time = time.time() - start_time
        print(f"✅ Small block 쓰기 성공: {write_time:.4f}초")
        
        # Small block 읽기 테스트
        print(f"📖 Small block 읽기 테스트...")
        start_time = time.time()
        read_data = nvme.read(len(small_data), lba=100)
        read_time = time.time() - start_time
        print(f"✅ Small block 읽기 성공: {read_time:.4f}초")
        
        # 데이터 검증
        if small_data == read_data:
            print("✅ 데이터 일치 확인")
        else:
            print("⚠️ 데이터 불일치")
            print(f"   원본: {small_data[:50]}")
            print(f"   읽음: {read_data[:50]}")
        
        # 디바이스 닫기
        nvme.close()
        print("✅ 디바이스 닫기 성공")
        
        return True
        
    except Exception as e:
        print(f"❌ Small block 테스트 실패: {e}")
        return False

def test_large_block():
    """max_blocks보다 큰 블록으로 테스트"""
    print("=" * 50)
    print("🔍 LARGE BLOCK 테스트 (max_blocks보다 큼)")
    print("=" * 50)
    
    device_path = "/dev/nvme0n1"
    
    try:
        # 디바이스 초기화
        print("📖 디바이스 초기화...")
        nvme = XNVMeNamespace(device_path, use_io_uring=True)
        print(f"✅ 디바이스 초기화 성공")
        print(f"📊 LBA 크기: {nvme.lba_nbytes} 바이트")
        print(f"📊 max_blocks: {nvme.max_blocks}")
        
        # Large block 테스트 (max_blocks * 2 = 64 블록 = 32KB)
        lba_size = nvme.lba_nbytes
        large_blocks = nvme.max_blocks * 2  # 64 블록
        large_size = large_blocks * lba_size  # 32KB
        # 정확히 large_size로 맞춤
        large_data = (b"LARGE_BLOCK_TEST_DATA" * ((large_size // len(b"LARGE_BLOCK_TEST_DATA")) + 1))[:large_size]
        assert len(large_data) == large_size
        
        print(f"📝 Large block 쓰기 테스트...")
        print(f"   - 데이터 크기: {len(large_data)} 바이트")
        print(f"   - 블록 수: {len(large_data) // lba_size}")
        print(f"   - max_blocks 대비: {len(large_data) // lba_size / nvme.max_blocks:.1f}배")
        
        start_time = time.time()
        nvme.write(large_data, lba=200)
        write_time = time.time() - start_time
        print(f"✅ Large block 쓰기 성공: {write_time:.4f}초")
        print(f"   - 쓰기 속도: {len(large_data) / write_time / 1024 / 1024:.2f} MB/s")
        
        # Large block 읽기 테스트
        print(f"📖 Large block 읽기 테스트...")
        start_time = time.time()
        read_data = nvme.read(len(large_data), lba=200)
        read_time = time.time() - start_time
        print(f"✅ Large block 읽기 성공: {read_time:.4f}초")
        print(f"   - 읽기 속도: {len(large_data) / read_time / 1024 / 1024:.2f} MB/s")
        
        # 데이터 검증 (처음과 끝 부분만)
        if (large_data[:100] == read_data[:100] and 
            large_data[-100:] == read_data[-100:]):
            print("✅ 데이터 일치 확인 (처음/끝 부분)")
        else:
            print("⚠️ 데이터 불일치")
            print(f"   원본 시작: {large_data[:50]}")
            print(f"   읽음 시작: {read_data[:50]}")
        
        # 디바이스 닫기
        nvme.close()
        print("✅ 디바이스 닫기 성공")
        
        return True
        
    except Exception as e:
        print(f"❌ Large block 테스트 실패: {e}")
        return False

def test_performance_comparison():
    """Small vs Large 블록 성능 비교"""
    print("=" * 50)
    print("🔍 성능 비교 테스트")
    print("=" * 50)
    
    device_path = "/dev/nvme0n1"
    
    try:
        # 디바이스 초기화
        nvme = XNVMeNamespace(device_path, use_io_uring=True)
        lba_size = nvme.lba_nbytes
        
        # Small block 성능 테스트 (10번 반복)
        small_data = b"SMALL_PERF_TEST" * 30
        small_data = small_data.ljust(lba_size, b"\x00")
        
        print("⏱️ Small block 성능 테스트 (10회 반복)...")
        small_write_times = []
        small_read_times = []
        
        for i in range(10):
            # 쓰기
            start_time = time.time()
            nvme.write(small_data, lba=300 + i)
            small_write_times.append(time.time() - start_time)
            
            # 읽기
            start_time = time.time()
            nvme.read(len(small_data), lba=300 + i)
            small_read_times.append(time.time() - start_time)
        
        avg_small_write = sum(small_write_times) / len(small_write_times)
        avg_small_read = sum(small_read_times) / len(small_read_times)
        
        print(f"   Small block 평균 쓰기 시간: {avg_small_write:.6f}초")
        print(f"   Small block 평균 읽기 시간: {avg_small_read:.6f}초")
        
        # Large block 성능 테스트 (5번 반복)
        large_blocks = nvme.max_blocks * 2
        large_size = large_blocks * lba_size
        large_data = b"LARGE_PERF_TEST" * (large_size // 16)
        large_data = large_data.ljust(large_size, b"\x00")
        
        print("⏱️ Large block 성능 테스트 (5회 반복)...")
        large_write_times = []
        large_read_times = []
        
        for i in range(5):
            # 쓰기
            start_time = time.time()
            nvme.write(large_data, lba=400 + i)
            large_write_times.append(time.time() - start_time)
            
            # 읽기
            start_time = time.time()
            nvme.read(len(large_data), lba=400 + i)
            large_read_times.append(time.time() - start_time)
        
        avg_large_write = sum(large_write_times) / len(large_write_times)
        avg_large_read = sum(large_read_times) / len(large_read_times)
        
        print(f"   Large block 평균 쓰기 시간: {avg_large_write:.6f}초")
        print(f"   Large block 평균 읽기 시간: {avg_large_read:.6f}초")
        
        # 성능 비교
        print("\n📊 성능 비교:")
        print(f"   Small block 쓰기 속도: {len(small_data) / avg_small_write / 1024:.2f} KB/s")
        print(f"   Large block 쓰기 속도: {len(large_data) / avg_large_write / 1024 / 1024:.2f} MB/s")
        print(f"   Small block 읽기 속도: {len(small_data) / avg_small_read / 1024:.2f} KB/s")
        print(f"   Large block 읽기 속도: {len(large_data) / avg_large_read / 1024 / 1024:.2f} MB/s")
        
        # 디바이스 닫기
        nvme.close()
        print("✅ 디바이스 닫기 성공")
        
        return True
        
    except Exception as e:
        print(f"❌ 성능 비교 테스트 실패: {e}")
        return False

def test_io_uring_small_large():
    print("=" * 50)
    print("🔍 IO_URING SMALL/LARGE BLOCK 테스트")
    print("=" * 50)
    device_path = "/dev/nvme0n1"
    try:
        nvme = XNVMeNamespace(device_path, use_io_uring=True)
        lba_size = nvme.lba_nbytes

        # Small block
        small_data = (b"IOURING_SMALL" * 100)[:lba_size]
        assert len(small_data) == lba_size
        print("📝 io_uring small block write...")
        nvme.write_uring(small_data, lba=1000)
        print("✅ io_uring small block write")
        read_data = nvme.read_uring(len(small_data), lba=1000)
        print("✅ io_uring small block read")
        if small_data == read_data:
            print("✅ io_uring small block 데이터 일치")
        else:
            print("❌ io_uring small block 데이터 불일치")

        # Large block
        large_blocks = nvme.max_blocks * 2
        large_size = large_blocks * lba_size
        large_data = (b"IOURING_LARGE" * ((large_size // len(b"IOURING_LARGE")) + 1))[:large_size]
        assert len(large_data) == large_size
        print("📝 io_uring large block write...")
        nvme.write_uring(large_data, lba=2000)
        print("✅ io_uring large block write")
        read_data = nvme.read_uring(len(large_data), lba=2000)
        print("✅ io_uring large block read")
        # 전체 비교 및 상세 불일치 출력
        if large_data == read_data:
            print("✅ io_uring large block 전체 데이터 일치")
        else:
            print("❌ io_uring large block 데이터 불일치 (전체)")
            # 처음/중간/끝 32바이트씩 hex로 출력
            def hex_dump(data, start, end):
                return ' '.join(f'{b:02x}' for b in data[start:end])
            print("  [원본 처음 32]:", hex_dump(large_data, 0, 32))
            print("  [읽음 처음 32]:", hex_dump(read_data, 0, 32))
            mid = len(large_data) // 2
            print("  [원본 중간 32]:", hex_dump(large_data, mid, mid+32))
            print("  [읽음 중간 32]:", hex_dump(read_data, mid, mid+32))
            print("  [원본 끝 32]:", hex_dump(large_data, -32, None))
            print("  [읽음 끝 32]:", hex_dump(read_data, -32, None))
            # 불일치 인덱스도 출력
            for i, (a, b) in enumerate(zip(large_data, read_data)):
                if a != b:
                    print(f"  [불일치 인덱스]: {i}, 원본={a:02x}, 읽음={b:02x}")
                    break
        nvme.close()
        print("✅ 디바이스 닫기 성공")
        return True
    except Exception as e:
        print(f"❌ io_uring 테스트 실패: {e}")
        return False

def test_sync_vs_io_uring():
    print("=" * 50)
    print("🔍 SYNC vs IO_URING 비교 테스트")
    print("=" * 50)
    device_path = "/dev/nvme0n1"
    try:
        # 같은 데이터 준비
        test_data = b"SYNC_VS_IOURING_TEST_DATA" * 100
        test_data = test_data[:512]  # 1블록으로 맞춤
        
        print("📝 테스트 1: sync 쓰기 + io_uring 읽기")
        nvme1 = XNVMeNamespace(device_path, use_io_uring=False)  # sync
        nvme2 = XNVMeNamespace(device_path, use_io_uring=True)   # io_uring
        
        # sync로 쓰기
        nvme1.write(test_data, lba=3000)
        print("✅ sync 쓰기 완료")
        
        # io_uring으로 읽기
        read_data = nvme2.read_uring(len(test_data), lba=3000)
        print("✅ io_uring 읽기 완료")
        
        if test_data == read_data:
            print("✅ sync 쓰기 + io_uring 읽기: 데이터 일치")
        else:
            print("❌ sync 쓰기 + io_uring 읽기: 데이터 불일치")
            print(f"  원본: {test_data[:32].hex()}")
            print(f"  읽음: {read_data[:32].hex()}")
        
        nvme1.close()
        nvme2.close()
        
        print("\n📝 테스트 2: io_uring 쓰기 + sync 읽기")
        nvme1 = XNVMeNamespace(device_path, use_io_uring=True)   # io_uring
        nvme2 = XNVMeNamespace(device_path, use_io_uring=False)  # sync
        
        # io_uring으로 쓰기
        nvme1.write_uring(test_data, lba=3001)
        print("✅ io_uring 쓰기 완료")
        
        # sync로 읽기
        read_data = nvme2.read(len(test_data), lba=3001)
        print("✅ sync 읽기 완료")
        
        if test_data == read_data:
            print("✅ io_uring 쓰기 + sync 읽기: 데이터 일치")
        else:
            print("❌ io_uring 쓰기 + sync 읽기: 데이터 불일치")
            print(f"  원본: {test_data[:32].hex()}")
            print(f"  읽음: {read_data[:32].hex()}")
        
        nvme1.close()
        nvme2.close()
        
        return True
        
    except Exception as e:
        print(f"❌ sync vs io_uring 비교 테스트 실패: {e}")
        return False

def main():
    print("🚀 NVMe Binding 테스트 시작")
    print("=" * 60)
    
    # 시스템 정보 출력
    print(f"🐍 Python 버전: {sys.version}")
    print(f"📁 현재 디렉토리: {os.getcwd()}")
    
    # NVMe 디바이스 확인
    device_path = "/dev/nvme0n1"
    if not os.path.exists(device_path):
        print(f"⚠️ {device_path} 디바이스가 없습니다.")
        print("💡 NVMe 디바이스를 확인하세요.")
        return
    
    print(f"💾 테스트 대상 디바이스: {device_path}")
    
    # 테스트 실행
    tests = [
        ("Small Block", test_small_block),
        ("Large Block", test_large_block),
        ("Performance Comparison", test_performance_comparison),
        ("IO_URING Small/Large", test_io_uring_small_large),
        ("Sync vs IO_URING", test_sync_vs_io_uring),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        if test_func():
            passed += 1
            print(f"✅ {test_name} 성공")
        else:
            print(f"❌ {test_name} 실패")
    
    print("\n" + "=" * 60)
    print(f"📊 테스트 결과: {passed}/{total} 성공")
    if passed == total:
        print("🎉 모든 테스트가 성공적으로 완료되었습니다!")
    else:
        print("⚠️ 일부 테스트가 실패했습니다.")

if __name__ == "__main__":
    main() 