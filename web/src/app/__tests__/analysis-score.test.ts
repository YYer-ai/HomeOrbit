import { describe, expect, it } from "vitest";

import { computeCompositeScore } from "../analysis-score";

describe("computeCompositeScore", () => {
  it("按启用分类的权重计算综合分", () => {
    expect(
      computeCompositeScore(
        { education: 80, healthcare: 40 },
        { education: 1, healthcare: 1 },
      ),
    ).toBe(60);
  });

  it("关闭分类后重新归一化剩余权重", () => {
    expect(
      computeCompositeScore(
        { education: 80, healthcare: 20, parks: 50 },
        { education: 1, healthcare: 0, parks: 3 },
      ),
    ).toBeCloseTo(57.5);
  });

  it("没有有效正权重时返回 null", () => {
    expect(computeCompositeScore({ education: 80 }, { education: 0 })).toBeNull();
    expect(computeCompositeScore({}, {})).toBeNull();
  });

  it.each([null, Number.NaN, Number.POSITIVE_INFINITY, "80", true])(
    "启用项分数为 %s 时返回 null",
    (score) => {
      expect(computeCompositeScore({ education: score }, { education: 1 })).toBeNull();
    },
  );

  it.each([-1, Number.NaN, Number.POSITIVE_INFINITY, "1", true])(
    "权重为 %s 时返回 null",
    (weight) => {
      expect(computeCompositeScore({ education: 80 }, { education: weight })).toBeNull();
    },
  );

  it("拒绝超出 0–100 的分数且忽略关闭项的缺失分数", () => {
    expect(computeCompositeScore({ education: -1 }, { education: 1 })).toBeNull();
    expect(computeCompositeScore({ education: 101 }, { education: 1 })).toBeNull();
    expect(
      computeCompositeScore(
        { education: 75, healthcare: null },
        { education: 1, healthcare: 0 },
      ),
    ).toBe(75);
  });

  it("有限的极大权重不会让综合分溢出", () => {
    const equal = computeCompositeScore(
      { education: 100, healthcare: 100 },
      { education: Number.MAX_VALUE, healthcare: Number.MAX_VALUE },
    );
    const weighted = computeCompositeScore(
      { education: 100, healthcare: 0 },
      { education: Number.MAX_VALUE, healthcare: Number.MAX_VALUE / 2 },
    );

    expect(equal).toBe(100);
    expect(weighted).toBeCloseTo(100 / 1.5);
    expect(Number.isFinite(equal)).toBe(true);
    expect(Number.isFinite(weighted)).toBe(true);
  });
});
