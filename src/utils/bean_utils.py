"""
对象属性复制工具模块

本模块提供了在对象之间、对象与字典之间复制属性的实用函数。
支持属性过滤（包含/排除）、自动跳过私有属性和方法。
"""

from typing import Any, Optional, List, Dict
import inspect


def copy_properties(
    source: Any,
    target: Any,
    exclude: Optional[List[str]] = None,
    include: Optional[List[str]] = None
) -> None:
    """
    将源对象的属性复制到目标对象
    
    复制source和target两者均具备的属性值。会自动跳过以下划线开头的私有属性、
    方法和函数。支持通过exclude和include参数进行属性过滤。
    
    参数:
        source: 源对象，从中读取属性值
        target: 目标对象，将属性值写入此对象
        exclude: 可选，要排除的属性名列表
        include: 可选，仅包含的属性名列表（如果指定，则只复制这些属性）
    
    返回值:
        None
    
    示例:
        >>> class Person:
        ...     def __init__(self, name, age):
        ...         self.name = name
        ...         self.age = age
        >>> 
        >>> source = Person("张三", 25)
        >>> target = Person("", 0)
        >>> copy_properties(source, target)
        >>> print(target.name, target.age)  # 输出: 张三 25
        >>> 
        >>> # 使用exclude排除某些属性
        >>> target2 = Person("", 0)
        >>> copy_properties(source, target2, exclude=["age"])
        >>> print(target2.name, target2.age)  # 输出: 张三 0
    """
    exclude = exclude or []
    
    try:
        # 获取源对象的所有属性
        source_attrs = dir(source)
        
        for attr_name in source_attrs:
            # 跳过私有属性（以下划线开头）
            if attr_name.startswith('_'):
                continue
            
            # 跳过排除列表中的属性
            if attr_name in exclude:
                continue
            
            # 如果指定了include，只处理include中的属性
            if include is not None and attr_name not in include:
                continue
            
            try:
                # 获取源属性值
                source_value = getattr(source, attr_name)
                
                # 跳过方法和函数
                if callable(source_value):
                    continue
                
                # 检查目标对象是否有该属性
                if hasattr(target, attr_name):
                    # 复制属性值到目标对象
                    setattr(target, attr_name, source_value)
                    
            except (AttributeError, TypeError) as e:
                # 忽略无法访问或设置的属性
                continue
                
    except Exception as e:
        # 处理其他异常
        pass


def copy_properties_dict(
    source: Any,
    target_dict: Dict[str, Any],
    exclude: Optional[List[str]] = None,
    include: Optional[List[str]] = None
) -> None:
    """
    将对象的属性复制到字典
    
    从源对象读取属性并存储到目标字典中。会自动跳过以下划线开头的私有属性、
    方法和函数。
    
    参数:
        source: 源对象，从中读取属性值
        target_dict: 目标字典，将属性值作为键值对存入此字典
        exclude: 可选，要排除的属性名列表
        include: 可选，仅包含的属性名列表（如果指定，则只复制这些属性）
    
    返回值:
        None（直接修改target_dict）
    
    示例:
        >>> class Product:
        ...     def __init__(self, name, price):
        ...         self.name = name
        ...         self.price = price
        >>> 
        >>> product = Product("笔记本", 5999)
        >>> result_dict = {}
        >>> copy_properties_dict(product, result_dict)
        >>> print(result_dict)  # 输出: {'name': '笔记本', 'price': 5999}
        >>> 
        >>> # 使用include只复制特定属性
        >>> result_dict2 = {}
        >>> copy_properties_dict(product, result_dict2, include=["name"])
        >>> print(result_dict2)  # 输出: {'name': '笔记本'}
    """
    exclude = exclude or []
    
    try:
        # 获取源对象的所有属性
        source_attrs = dir(source)
        
        for attr_name in source_attrs:
            # 跳过私有属性（以下划线开头）
            if attr_name.startswith('_'):
                continue
            
            # 跳过排除列表中的属性
            if attr_name in exclude:
                continue
            
            # 如果指定了include，只处理include中的属性
            if include is not None and attr_name not in include:
                continue
            
            try:
                # 获取源属性值
                source_value = getattr(source, attr_name)
                
                # 跳过方法和函数
                if callable(source_value):
                    continue
                
                # 将属性值存入字典
                target_dict[attr_name] = source_value
                
            except (AttributeError, TypeError) as e:
                # 忽略无法访问的属性
                continue
                
    except Exception as e:
        # 处理其他异常
        pass


def copy_properties_from_dict(
    source_dict: Dict[str, Any],
    target: Any,
    exclude: Optional[List[str]] = None,
    include: Optional[List[str]] = None
) -> None:
    """
    从字典复制属性到对象
    
    从源字典读取键值对并设置到目标对象的对应属性。只会设置目标对象已存在的属性。
    
    参数:
        source_dict: 源字典，从中读取键值对
        target: 目标对象，将字典的值设置到此对象的属性
        exclude: 可选，要排除的属性名列表
        include: 可选，仅包含的属性名列表（如果指定，则只复制这些属性）
    
    返回值:
        None
    
    示例:
        >>> class Book:
        ...     def __init__(self):
        ...         self.title = ""
        ...         self.author = ""
        ...         self.price = 0
        >>> 
        >>> book_dict = {"title": "Python编程", "author": "张三", "price": 89}
        >>> book = Book()
        >>> copy_properties_from_dict(book_dict, book)
        >>> print(book.title, book.author, book.price)  # 输出: Python编程 张三 89
        >>> 
        >>> # 使用exclude排除某些属性
        >>> book2 = Book()
        >>> copy_properties_from_dict(book_dict, book2, exclude=["price"])
        >>> print(book2.title, book2.author, book2.price)  # 输出: Python编程 张三 0
    """
    exclude = exclude or []
    
    try:
        for key, value in source_dict.items():
            # 跳过私有属性（以下划线开头）
            if key.startswith('_'):
                continue
            
            # 跳过排除列表中的属性
            if key in exclude:
                continue
            
            # 如果指定了include，只处理include中的属性
            if include is not None and key not in include:
                continue
            
            try:
                # 检查目标对象是否有该属性
                if hasattr(target, key):
                    # 设置属性值
                    setattr(target, key, value)
                    
            except (AttributeError, TypeError) as e:
                # 忽略无法设置的属性
                continue
                
    except Exception as e:
        # 处理其他异常
        pass


if __name__ == "__main__":
    print("=== 对象属性复制工具示例 ===\n")
    
    # 示例1: 对象到对象的复制
    print("示例1: 对象到对象的复制")
    
    class Person:
        def __init__(self, name="", age=0, city=""):
            self.name = name
            self.age = age
            self.city = city
        
        def greet(self):
            return f"你好，我是{self.name}"
    
    source_person = Person("李明", 30, "北京")
    target_person = Person()
    
    copy_properties(source_person, target_person)
    print(f"复制后: {target_person.name}, {target_person.age}, {target_person.city}")
    
    # 示例2: 使用exclude排除属性
    print("\n示例2: 使用exclude排除age属性")
    target_person2 = Person()
    copy_properties(source_person, target_person2, exclude=["age"])
    print(f"复制后: {target_person2.name}, {target_person2.age}, {target_person2.city}")
    
    # 示例3: 使用include只复制特定属性
    print("\n示例3: 使用include只复制name属性")
    target_person3 = Person()
    copy_properties(source_person, target_person3, include=["name"])
    print(f"复制后: {target_person3.name}, {target_person3.age}, {target_person3.city}")
    
    # 示例4: 对象到字典的复制
    print("\n示例4: 对象到字典的复制")
    person_dict = {}
    copy_properties_dict(source_person, person_dict)
    print(f"字典内容: {person_dict}")
    
    # 示例5: 字典到对象的复制
    print("\n示例5: 字典到对象的复制")
    data_dict = {"name": "王芳", "age": 25, "city": "上海"}
    target_person4 = Person()
    copy_properties_from_dict(data_dict, target_person4)
    print(f"复制后: {target_person4.name}, {target_person4.age}, {target_person4.city}")
    
    print("\n=== 示例运行完成 ===")